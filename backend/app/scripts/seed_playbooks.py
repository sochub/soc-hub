#!/usr/bin/env python3
"""Seed the GLOBAL playbook marketplace catalog (system templates, tenant_id NULL).

Idempotent: skips any system template whose name already exists. Tenants import
from this catalog via POST /api/v1/playbooks/import.

Usage (in the backend container):
    python -m app.scripts.seed_playbooks
"""
import asyncio

import app.db.base  # noqa: F401
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.playbook import PlaybookTemplate, PlaybookTaskTemplate

# Each template: name, category, description, tasks=[(phase, title, description)]
CATALOG = [
    {
        "name": "Phishing (T1566)", "category": "phishing",
        "description": "Suspicious or malicious email reported by a user or detected by the mail gateway.",
        "tasks": [
            ("identification", "Confirm the report and collect the email", "Get the original .eml/headers from the reporter; do not forward as inline."),
            ("identification", "Extract sender, reply-to, URLs, attachments", "Record all indicators; defang URLs."),
            ("identification", "Determine scope — who else received it", "Search the mail gateway for the same sender/subject/URL."),
            ("containment", "Block sender domain / address at the gateway", None),
            ("containment", "Quarantine / purge matching messages", "Pull the message from all affected mailboxes."),
            ("containment", "Block malicious URLs and domains at the proxy/DNS", None),
            ("eradication", "Reset credentials for any user who entered them", None),
            ("eradication", "Remove any dropped payloads from endpoints", None),
            ("recovery", "Confirm mail flow is clean and users have access", None),
            ("lessons_learned", "Update detection rules and user-awareness notes", None),
        ],
    },
    {
        "name": "Malware Infection", "category": "malware",
        "description": "Endpoint malware detected by EDR/AV or observed behaviour.",
        "tasks": [
            ("identification", "Identify affected host(s) and malware family", "Pull EDR detection details and hashes."),
            ("identification", "Collect IOCs (hashes, C2 IPs/domains, persistence)", None),
            ("containment", "Isolate the affected host from the network", None),
            ("containment", "Block C2 indicators at network controls", None),
            ("eradication", "Remove malware and persistence mechanisms", None),
            ("eradication", "Scan related hosts for the same IOCs", None),
            ("recovery", "Reimage or restore the host and validate", None),
            ("recovery", "Return the host to production and monitor", None),
            ("lessons_learned", "Document root cause and tune detections", None),
        ],
    },
    {
        "name": "Ransomware (T1486)", "category": "ransomware",
        "description": "Data-encryption / ransomware activity on one or more hosts.",
        "tasks": [
            ("identification", "Confirm ransomware and identify the variant", "Ransom note, extension, known TTPs."),
            ("identification", "Determine blast radius and encrypted assets", None),
            ("identification", "Preserve evidence (memory, ransom note, samples)", None),
            ("containment", "Isolate affected hosts immediately", None),
            ("containment", "Disable affected accounts and shared drives", None),
            ("containment", "Block lateral-movement paths (SMB/RDP)", None),
            ("eradication", "Remove ransomware binaries and persistence", None),
            ("eradication", "Reset compromised credentials org-wide if needed", None),
            ("recovery", "Restore from known-good backups", "Verify backup integrity before restore."),
            ("recovery", "Validate systems and monitor for re-infection", None),
            ("lessons_learned", "Post-incident review; backup & segmentation gaps", None),
        ],
    },
    {
        "name": "Unauthorized / VPN Access (T1133)", "category": "unauthorized-access",
        "description": "Suspicious external remote access (VPN, RDP, external services).",
        "tasks": [
            ("identification", "Identify the account and source IP/geo", None),
            ("identification", "Review auth logs for impossible travel / MFA bypass", None),
            ("containment", "Disable or force re-auth on the account", None),
            ("containment", "Block the source IP at the perimeter", None),
            ("eradication", "Terminate active sessions and rotate credentials", None),
            ("eradication", "Check for persistence created during access", None),
            ("recovery", "Re-enable access with MFA and monitor", None),
            ("lessons_learned", "Review access policy and MFA coverage", None),
        ],
    },
    {
        "name": "Data Exfiltration", "category": "exfiltration",
        "description": "Suspected large or anomalous outbound data transfer.",
        "tasks": [
            ("identification", "Identify source host/user and destination", None),
            ("identification", "Quantify the data and channel (DNS, HTTPS, cloud)", None),
            ("containment", "Block the destination and channel", None),
            ("containment", "Isolate the source host", None),
            ("eradication", "Remove exfil tooling / persistence", None),
            ("recovery", "Assess data sensitivity and notify stakeholders", None),
            ("lessons_learned", "DLP tuning and egress-control review", None),
        ],
    },
    {
        "name": "Password Spraying (T1110.003)", "category": "credential-access",
        "description": "Distributed low-and-slow authentication attempts across many accounts.",
        "tasks": [
            ("identification", "Identify targeted accounts and source IPs", None),
            ("identification", "Check for any successful logins", None),
            ("containment", "Block source IPs and enforce lockout policy", None),
            ("containment", "Force password reset on any compromised accounts", None),
            ("eradication", "Terminate sessions on affected accounts", None),
            ("recovery", "Confirm accounts are secured; enable MFA", None),
            ("lessons_learned", "Review password policy and MFA enforcement", None),
        ],
    },
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        existing = {
            n for (n,) in (await db.execute(
                select(PlaybookTemplate.name).where(
                    PlaybookTemplate.tenant_id.is_(None), PlaybookTemplate.is_system == True  # noqa: E712
                )
            )).all()
        }
        created = 0
        for tmpl in CATALOG:
            if tmpl["name"] in existing:
                continue
            t = PlaybookTemplate(
                tenant_id=None, is_system=True, name=tmpl["name"],
                category=tmpl["category"], description=tmpl["description"],
            )
            db.add(t)
            await db.flush()
            for i, (phase, title, desc) in enumerate(tmpl["tasks"]):
                db.add(PlaybookTaskTemplate(
                    template_id=t.id, phase=phase, title=title, description=desc, order=i,
                ))
            created += 1
        await db.commit()
        print(f"Marketplace seeding done: {created} new template(s), {len(existing)} already present.")


if __name__ == "__main__":
    asyncio.run(seed())
