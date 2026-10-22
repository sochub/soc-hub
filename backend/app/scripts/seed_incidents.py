#!/usr/bin/env python3
"""Seed synthetic incidents spread across the last N days (demo / dashboard data).

Every seeded case is tagged "seed" so it can be found and removed later.

Usage (inside the backend container):
    python -m app.scripts.seed_incidents --tenant-id 2 --count 40 --days 30 --alerts 8
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

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.case import Alert, Case, CaseStatus, CaseSeverity, TimelineEvent
from app.models.artifact import Artifact, ArtifactType
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User

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

# Small shared pools so values repeat across cases (drives correlation & top-indicators).
# Synthetic only: RFC 5737 documentation IPs, RFC 2606 reserved domains, made-up hashes.
IPS = ["192.0.2.45", "192.0.2.118", "198.51.100.23", "198.51.100.77", "203.0.113.9", "203.0.113.200"]
DOMAINS = ["update-portal.example", "file-share.example", "cdn-track.example.net", "login-verify.test", "pay-invoice.example.org"]
HASHES = ["0a1b2c3d4e5f60718293a4b5c6d7e8f9", "f0e1d2c3b4a5968778695a4b3c2d1e0f", "1111aaaa2222bbbb3333cccc4444dddd", "abcdef0123456789abcdef0123456789"]
EMAILS = ["billing@update-portal.example", "it-support@login-verify.test", "no-reply@pay-invoice.example.org"]
URLS = ["http://update-portal.example/auth/reset", "http://file-share.example/d/invoice.zip"]

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


# Lifecycle order; a case walks this path up to its final status, one status_change per hop.
PATH = [CaseStatus.NEW, CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.PENDING, CaseStatus.RESOLVED, CaseStatus.CLOSED]
# Notes logged while the case sits in a status (event_type, markdown content).
STAGE_NOTES = {
    CaseStatus.OPEN: [("comment", "Picked up. Reviewing the **{source}** signal and pulling related logs.")],
    CaseStatus.IN_PROGRESS: [
        ("investigation", "Scoping:\n- Checked auth logs for the last 24h\n- Pivoted on `{value}` across the fleet\n- No other hosts matched so far"),
        ("containment", "Contained: blocked `{value}` at the perimeter and isolated the affected endpoint."),
    ],
    CaseStatus.PENDING: [("comment", "Waiting on the asset owner to confirm the activity was not expected.")],
    CaseStatus.RESOLVED: [("remediation", "Remediated: credentials rotated, persistence removed, host re-imaged.\n\n> Verified clean by EDR rescan.")],
}
OUTCOMES = ["true_positive_with_impact", "true_positive_without_impact", "false_positive", "legitimate"]
ALERT_TITLES = [
    ("EDR", "Suspicious PowerShell spawned by Office on {host}"),
    ("SIEM", "Impossible travel sign-in for a {dept} user"),
    ("Email Gateway", "Credential-phishing link clicked from {dept}"),
    ("Firewall", "Outbound connection to known C2 {ip}"),
    ("IDS", "Port scan from {ip} against the DMZ"),
    ("Threat Intel", "Indicator match: {domain}"),
]


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
    alerts = await db.execute(delete(Alert).where(Alert.tenant_id == tenant_id, Alert.external_id.like("seed-%")))
    if not seed_ids:
        await db.commit()
        print(f"No seeded cases to purge; removed {alerts.rowcount} seeded alert(s).")
        return
    await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(seed_ids)))
    await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(seed_ids)))
    await db.execute(delete(IOC).where(IOC.case_id.in_(seed_ids)))
    await db.execute(delete(Case).where(Case.id.in_(seed_ids)))
    await db.commit()
    print(f"Purged {len(seed_ids)} seeded case(s) from tenant {tenant_id}.")


async def seed(db, tenant_id: int, count: int, days: int, alerts: int = 0) -> None:
    now = datetime.now(timezone.utc)

    # Pick a member of the tenant to own the cases (or None).
    owner = (await db.execute(
        select(TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id).limit(1)
    )).scalars().first()
    if owner is None:  # empty tenant: attribute activity to a super admin so the timeline shows an actor
        owner = (await db.execute(select(User.id).where(User.is_super_admin.is_(True)).order_by(User.id).limit(1))).scalars().first()

    # Even status mix: repeat the weighted list to `count` and shuffle (random.choice can skew small runs).
    statuses = (STATUSES * (count // len(STATUSES) + 1))[:count]
    random.shuffle(statuses)
    severities = (SEVERITIES * (count // len(SEVERITIES) + 1))[:count]
    random.shuffle(severities)
    seeded = []

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
    for status, severity in zip(statuses, severities):
        tmpl, t1, t2 = random.choice(TEMPLATES)
        title = tmpl.format(host=random.choice(HOSTS), dept=random.choice(DEPTS),
                            ip=random.choice(IPS), domain=random.choice(DOMAINS))
        created_at = _rand_dt(now, days)
        # Hops up to the final status, spread between creation and resolution (or now).
        hops = PATH[:PATH.index(status) + 1]
        if status in CLOSED and random.random() < 0.5:
            hops = [h for h in hops if h != CaseStatus.PENDING]
        span = (min(now, created_at + timedelta(hours=random.randint(2, 120))) if status in CLOSED else now) - created_at
        times = sorted(created_at + span * random.uniform(0.02, 1) for _ in hops[1:])
        resolved_at = next((t for h, t in zip(hops[1:], times) if h in CLOSED), None)

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
            acknowledged_at=times[0] if times else None,
            outcome=random.choice(OUTCOMES) if status in CLOSED else None,
            closing_note="Root cause identified and remediated; monitoring for recurrence." if status == CaseStatus.CLOSED else None,
        )
        db.add(case)
        await db.flush()

        # attach 1-2 artifacts (shared pool → correlation/top-indicators populate)
        kinds = [k for k in (t1, t2) if k]
        linked = []
        for kind in kinds:
            art = await get_artifact(kind)
            linked.append(art)
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

        # Lifecycle: a status_change per hop (same text the API writes) plus notes while in each status.
        value = linked[0].value if linked else random.choice(IPS)
        ends = times[1:] + [min(now, (times[-1] if times else now) + timedelta(hours=4))]
        for prev, hop, at, until in zip(hops, hops[1:], times, ends):
            content = f"Status changed from {prev.value} to {hop.value}"
            if hop == CaseStatus.CLOSED and case.outcome:
                content += f"\n\n**Outcome:** {case.outcome.replace('_', ' ')}\n\n**Closing note:** {case.closing_note}"
            db.add(TimelineEvent(case_id=case.id, user_id=owner, event_type="status_change", content=content, created_at=at))
            for i, (etype, text) in enumerate(STAGE_NOTES.get(hop, [])):
                note_at = at + (until - at) * (i + 1) / (len(STAGE_NOTES[hop]) + 1)
                db.add(TimelineEvent(case_id=case.id, user_id=owner, event_type=etype, created_at=note_at,
                                     content=text.format(source=case.source, value=value)))
        seeded.append(case)
        created += 1

    for i in range(alerts):
        source, tmpl = ALERT_TITLES[i % len(ALERT_TITLES)]
        title = tmpl.format(host=random.choice(HOSTS), dept=random.choice(DEPTS),
                            ip=random.choice(IPS), domain=random.choice(DOMAINS))
        # mostly untriaged so the Alerts queue has work; a couple promoted / dismissed for history
        state = "pending" if i < max(1, alerts - 3) else ("promoted" if i % 2 else "dismissed")
        case = random.choice(seeded) if state == "promoted" and seeded else None
        if state == "promoted" and case is None:
            state = "pending"
        db.add(Alert(
            tenant_id=tenant_id, source=source, external_id=f"seed-{tenant_id}-{i}", title=title,
            severity=random.choice(SEVERITIES).value, status=state,
            case_id=case.id if case else None,
            dismiss_reason="Benign: scheduled vulnerability scan." if state == "dismissed" else None,
            payload={"seed": True, "source": source, "title": title, "observed_at": _rand_dt(now, 2).isoformat()},
            created_at=_rand_dt(now, 3),
        ))

    await db.commit()
    print(f"Seeded {alerts} alert(s).")
    print(f"Seeded {created} incident(s) into tenant {tenant_id} over the last {days} days.")
    print(f"All tagged '{SEED_TAG}'. Remove with: --purge")


async def main_async(args) -> None:
    async with AsyncSessionLocal() as db:
        tenant_id = await _resolve_tenant_id(db, args.tenant_id)
        if args.purge:
            await purge(db, tenant_id)
        else:
            await seed(db, tenant_id, args.count, args.days, args.alerts)


def main():
    # Seeds must never trigger real threat-intel lookups: the session hooks would otherwise enqueue
    # enrichment for every seeded IOC/artifact and send synthetic values to external services.
    settings.ENRICHMENT_ENABLED = False
    parser = argparse.ArgumentParser(description="Seed synthetic incidents for the dashboard")
    parser.add_argument("--tenant-id", type=int, default=None, help="Tenant to seed (default: lowest id)")
    parser.add_argument("--count", type=int, default=40, help="Number of incidents (default 40)")
    parser.add_argument("--days", type=int, default=30, help="Spread across the last N days (default 30)")
    parser.add_argument("--alerts", type=int, default=0, help="Number of alerts to seed (default 0)")
    parser.add_argument("--purge", action="store_true", help="Remove previously seeded incidents instead")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
