#!/usr/bin/env python3
"""Seed synthetic incidents spread across the last N days (demo / dashboard data).

Every seeded case is tagged "seed" so it can be found and removed later.

Usage (inside the backend container):
    python -m app.scripts.seed_incidents --tenant-id 2 --count 40 --days 30
    python -m app.scripts.seed_incidents --tenant-id 2 --purge      # remove seeded data

Cases are backdated (created_at / resolved_at set to past timestamps) so the
trend chart, MTTR, and aging buckets reflect a realistic month of activity.
"""
import argparse
import asyncio
import random
from datetime import datetime, timedelta, timezone

import app.db.base  # noqa: F401 — register models
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseStatus, CaseSeverity, TimelineEvent
from app.models.artifact import Artifact, ArtifactType
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.membership import TenantMembership
from app.models.tenant import Tenant

SEED_TAG = "seed"

# Weighted distributions
SEVERITIES = (
    [CaseSeverity.CRITICAL] * 1 + [CaseSeverity.HIGH] * 3 + [CaseSeverity.MEDIUM] * 4
    + [CaseSeverity.LOW] * 2 + [CaseSeverity.INFO] * 1
)
STATUSES = (
    [CaseStatus.NEW] * 3 + [CaseStatus.OPEN] * 4 + [CaseStatus.IN_PROGRESS] * 3
    + [CaseStatus.PENDING] * 2 + [CaseStatus.RESOLVED] * 4 + [CaseStatus.CLOSED] * 2
)
CLOSED = {CaseStatus.RESOLVED, CaseStatus.CLOSED}
SOURCES = ["EDR", "SIEM", "Firewall", "Email Gateway", "Threat Intel", "Manual", "IDS"]

HOSTS = ["WS-FIN-014", "WS-HR-007", "SRV-DC01", "SRV-WEB02", "LT-DEV-221", "WS-MKT-033", "SRV-DB03"]
DEPTS = ["Finance", "HR", "Engineering", "Marketing", "Legal", "Operations"]

# Small shared pools so values repeat across cases (drives correlation & top-indicators)
IPS = ["185.220.101.45", "45.137.21.9", "103.224.182.250", "91.219.236.18", "194.165.16.77", "5.188.206.130"]
DOMAINS = ["secure-update-portal.com", "drive-share-files.net", "cdn-analytics-track.io", "login-verify-account.co", "pay-invoice-now.org"]
HASHES = ["d41d8cd98f00b204e9800998ecf8427e", "5f4dcc3b5aa765d61d8327deb882cf99", "098f6bcd4621d373cade4e832627b4f6", "e99a18c428cb38d5f260853678922e03"]
EMAILS = ["billing@secure-update-portal.com", "it-support@login-verify-account.co", "no-reply@pay-invoice-now.org"]
URLS = ["http://secure-update-portal.com/auth/reset", "http://drive-share-files.net/d/invoice.zip"]

TEMPLATES = [
    ("Phishing email targeting {dept}", "email", "url"),
    ("Malware detected on {host}", "file_hash", "ip"),
    ("Brute-force login attempts from {ip}", "ip", None),
    ("Suspicious C2 beaconing to {domain}", "domain", "ip"),
    ("Possible data exfiltration via {domain}", "domain", None),
    ("Ransomware indicators on {host}", "file_hash", "ip"),
    ("Unauthorized VPN access from {ip}", "ip", None),
    ("Credential stuffing against {host}", "ip", None),
    ("Suspicious PowerShell on {host}", "file_hash", None),
    ("DNS tunneling detected to {domain}", "domain", None),
    ("Anomalous data transfer from {host}", "ip", "domain"),
    ("Impossible-travel sign-in for {dept} user", "ip", None),
]

ARTIFACT_TYPE_MAP = {
    "ip": (ArtifactType.IP, IPS, "ip_address"),
    "domain": (ArtifactType.DOMAIN, DOMAINS, "domain"),
    "file_hash": (ArtifactType.FILE_HASH, HASHES, "file_hash"),
    "email": (ArtifactType.EMAIL, EMAILS, "email"),
    "url": (ArtifactType.URL, URLS, "url"),
}


def _rand_dt(now: datetime, days: int) -> datetime:
    """A random timestamp within the last `days`."""
    secs = random.randint(0, days * 86400)
    return now - timedelta(seconds=secs)


async def _resolve_tenant_id(db, tenant_id):
    if tenant_id is not None:
        t = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
        if not t:
            raise SystemExit(f"Tenant {tenant_id} not found.")
        return tenant_id
    t = (await db.execute(select(Tenant.id).order_by(Tenant.id).limit(1))).scalars().first()
    if not t:
        raise SystemExit("No tenants exist. Create a tenant first.")
    print(f"No --tenant-id given; using tenant {t}.")
    return t


async def purge(db, tenant_id: int) -> None:
    rows = await db.execute(select(Case.id).where(Case.tenant_id == tenant_id))
    case_ids = [r for (r,) in rows.all()]
    # Only purge ones tagged seed
    seed_rows = await db.execute(select(Case.id, Case.tags).where(Case.tenant_id == tenant_id))
    seed_ids = [cid for cid, tags in seed_rows.all() if tags and SEED_TAG in tags]
    if not seed_ids:
        print("No seeded cases to purge.")
        return
    await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(seed_ids)))
    await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(seed_ids)))
    await db.execute(delete(IOC).where(IOC.case_id.in_(seed_ids)))
    await db.execute(delete(Case).where(Case.id.in_(seed_ids)))
    await db.commit()
    print(f"Purged {len(seed_ids)} seeded case(s) from tenant {tenant_id}.")


async def seed(db, tenant_id: int, count: int, days: int) -> None:
    now = datetime.now(timezone.utc)

    # Pick a member of the tenant to own the cases (or None).
    owner = (await db.execute(
        select(TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id).limit(1)
    )).scalars().first()

    # cache of (value,type) -> artifact id for reuse across cases
    artifact_cache: dict = {}

    async def get_artifact(kind: str) -> Artifact:
        atype, pool, _ = ARTIFACT_TYPE_MAP[kind]
        value = random.choice(pool)
        key = (value, atype)
        if key in artifact_cache:
            return artifact_cache[key]
        existing = (await db.execute(
            select(Artifact).where(
                Artifact.value == value, Artifact.artifact_type == atype,
                Artifact.tenant_id == tenant_id, Artifact.isolated == False,  # noqa: E712
            )
        )).scalars().first()
        if not existing:
            existing = Artifact(artifact_type=atype, value=value, tenant_id=tenant_id,
                                isolated=False, created_by=owner)
            db.add(existing)
            await db.flush()
        artifact_cache[key] = existing
        return existing

    created = 0
    for _ in range(count):
        tmpl, t1, t2 = random.choice(TEMPLATES)
        title = tmpl.format(host=random.choice(HOSTS), dept=random.choice(DEPTS),
                            ip=random.choice(IPS), domain=random.choice(DOMAINS))
        severity = random.choice(SEVERITIES)
        status = random.choice(STATUSES)
        created_at = _rand_dt(now, days)
        resolved_at = None
        if status in CLOSED:
            resolved_at = min(now, created_at + timedelta(hours=random.randint(2, 240)))

        case = Case(
            title=title,
            description=f"Auto-generated demo incident. Source signal flagged {title.lower()}.",
            severity=severity,
            status=status,
            source=random.choice(SOURCES),
            tags=[SEED_TAG, severity.value],
            tenant_id=tenant_id,
            owner_id=owner,
            created_at=created_at,
            resolved_at=resolved_at,
        )
        db.add(case)
        await db.flush()

        # attach 1-2 artifacts (shared pool → correlation/top-indicators populate)
        kinds = [k for k in (t1, t2) if k]
        for kind in kinds:
            art = await get_artifact(kind)
            dup = (await db.execute(
                select(CaseArtifact).where(
                    CaseArtifact.case_id == case.id, CaseArtifact.artifact_id == art.id)
            )).scalars().first()
            if not dup:
                db.add(CaseArtifact(case_id=case.id, artifact_id=art.id, added_by=owner))

        # an IOC for some cases (feeds IOC insights)
        if t1 in ARTIFACT_TYPE_MAP and random.random() < 0.7:
            _, pool, ioc_type = ARTIFACT_TYPE_MAP[t1]
            db.add(IOC(
                tenant_id=tenant_id, case_id=case.id, ioc_type=ioc_type,
                value=random.choice(pool), threat_level=severity.value,
                confidence=random.randint(50, 95),
                status=random.choice(["active", "active", "resolved"]),
                tlp=random.choice(["amber", "red", "green"]),
                first_seen=created_at, last_seen=now, source="seed",
                created_by=owner,
            ))

        # 1-3 timeline events between creation and now
        for _ in range(random.randint(1, 3)):
            etype = random.choice(["comment", "investigation", "status_change", "containment"])
            ev_at = created_at + timedelta(seconds=random.randint(0, max(1, int((now - created_at).total_seconds()))))
            db.add(TimelineEvent(
                case_id=case.id, user_id=owner, event_type=etype,
                content=f"{etype.replace('_', ' ').title()} step recorded by analyst.",
                created_at=min(ev_at, now),
            ))
        created += 1

    await db.commit()
    print(f"Seeded {created} incident(s) into tenant {tenant_id} over the last {days} days.")
    print(f"All tagged '{SEED_TAG}'. Remove with: --purge")


async def main_async(args) -> None:
    async with AsyncSessionLocal() as db:
        tenant_id = await _resolve_tenant_id(db, args.tenant_id)
        if args.purge:
            await purge(db, tenant_id)
        else:
            await seed(db, tenant_id, args.count, args.days)


def main():
    parser = argparse.ArgumentParser(description="Seed synthetic incidents for the dashboard")
    parser.add_argument("--tenant-id", type=int, default=None, help="Tenant to seed (default: lowest id)")
    parser.add_argument("--count", type=int, default=40, help="Number of incidents (default 40)")
    parser.add_argument("--days", type=int, default=30, help="Spread across the last N days (default 30)")
    parser.add_argument("--purge", action="store_true", help="Remove previously seeded incidents instead")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
