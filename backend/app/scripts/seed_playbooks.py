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
    {
        "name": "Compromised Identity (T1078)", "category": "identity",
        "description": "Valid account abused by an attacker after credential theft or session hijack.",
        "tasks": [
            ("identification", "Review sign-in history for the user", "Query the Okta System Log or Entra ID sign-in logs for unfamiliar IPs, devices, and user agents."),
            ("identification", "Scope post-login activity", "Search the Microsoft 365 unified audit log and app access for actions taken by the account since first anomalous sign-in."),
            ("containment", "Suspend the account and clear sessions", "Suspend the user in Okta and clear sessions, or disable the account and revoke sign-in sessions in Entra ID."),
            ("containment", "Revoke tokens and API keys", "Revoke refresh tokens, app passwords, and personal API tokens issued to the account."),
            ("eradication", "Reset credentials and MFA factors", "Force a password reset and reset all enrolled MFA factors in Okta or Entra ID after verifying the user out of band."),
            ("eradication", "Remove attacker persistence", "Delete rogue MFA devices, inbox rules, forwarding addresses, OAuth consents, and new admin role assignments."),
            ("recovery", "Re-enable access with step-up verification", "Restore the account once clean and confirm normal sign-in patterns for at least 72 hours."),
            ("lessons_learned", "Identify the credential source and harden", "Determine how credentials were obtained and tighten conditional access, phishing-resistant MFA, and session lifetimes."),
        ],
    },
    {
        "name": "Business Email Compromise (T1114)", "category": "email-security",
        "description": "Mailbox takeover or spoofing used for payment fraud or data theft.",
        "tasks": [
            ("identification", "Review mailbox inbox rules and forwarding", "Check inbox rules, forwarding addresses, and delegates on the mailbox in Exchange admin center or Microsoft Defender XDR."),
            ("identification", "Search the unified audit log for mailbox activity", "Search the Microsoft 365 unified audit log for MailItemsAccessed, Send, and New-InboxRule events by the account."),
            ("containment", "Block sign-in and revoke sessions", "Disable the account and revoke sign-in sessions in Entra ID, or suspend the user and clear sessions in Okta."),
            ("containment", "Halt pending payments and notify finance", "Contact finance and the bank to hold or recall any wire or invoice change requests tied to the thread."),
            ("eradication", "Remove malicious rules, forwards, and delegates", "Delete attacker-created inbox rules, external forwarding, and mailbox permissions."),
            ("eradication", "Purge related messages", "Use Defender XDR threat explorer to soft-delete phishing and fraudulent messages across mailboxes."),
            ("recovery", "Reset credentials and re-enroll MFA", "Reset the password and MFA factors, then restore access after the user confirms their identity."),
            ("lessons_learned", "Notify affected parties and tune controls", "Notify impacted partners, and add external-forward blocking, impersonation protection, and payment verification controls."),
        ],
    },
    {
        "name": "MFA Fatigue / Push Bombing (T1621)", "category": "identity",
        "description": "Attacker floods a user with MFA push prompts until one is approved.",
        "tasks": [
            ("identification", "Pull MFA push history", "Query the Okta System Log (user.mfa.okta_verify.deny_push, push sent events) or Entra ID sign-in logs for repeated MFA prompts."),
            ("identification", "Confirm whether a push was approved", "Correlate any approved prompt with the source IP and subsequent sign-in and application activity."),
            ("containment", "Suspend the user and clear sessions", "Suspend the user in Okta and clear sessions, or revoke sign-in sessions in Entra ID, if a push was approved."),
            ("containment", "Block the attacking source IP", "Add the source IP to a network zone blocklist in Okta or a named location block in Entra Conditional Access."),
            ("eradication", "Reset password and MFA factors", "Reset the password and enrolled factors for the user after verifying identity out of band."),
            ("recovery", "Re-enroll with phishing-resistant factors", "Re-enroll the user with number matching or FIDO2 and confirm normal activity."),
            ("lessons_learned", "Enable number matching and push limits", "Turn on number matching, additional context, and push rate limits, and brief users on reporting unexpected prompts."),
        ],
    },
    {
        "name": "EDR Detection Triage", "category": "endpoint",
        "description": "Triage and respond to an alert raised by the endpoint detection platform.",
        "tasks": [
            ("identification", "Review the detection in the console", "Open the CrowdStrike Falcon detection or SentinelOne threat and review the process tree, command line, and file hashes."),
            ("identification", "Determine true or false positive", "Check hash reputation, parent process, user context, and whether the behavior is known admin or software activity."),
            ("identification", "Scope across the environment", "Search for the same hash, IOC, or behavior on other hosts using Falcon event search or SentinelOne Deep Visibility."),
            ("containment", "Isolate the host", "Network contain the host in CrowdStrike Falcon, or disconnect it from the network in SentinelOne."),
            ("eradication", "Collect evidence and remove the threat", "Use Falcon RTR to gather artifacts and kill malicious processes, or run SentinelOne remediate and rollback."),
            ("eradication", "Hunt for persistence", "Check scheduled tasks, services, run keys, and startup items for related persistence."),
            ("recovery", "Lift containment and verify", "Release network containment or reconnect the host and monitor for recurring detections."),
            ("lessons_learned", "Tune detections and document", "Add exclusions or custom IOA rules where warranted and record the verdict and findings in the case."),
        ],
    },
    {
        "name": "Illicit OAuth Consent Grant (T1528)", "category": "cloud-security",
        "description": "Malicious or over-privileged OAuth app granted access to user data.",
        "tasks": [
            ("identification", "Review enterprise app consents", "In Entra ID, review Enterprise applications and user and admin consents for unfamiliar apps and high-risk permissions."),
            ("identification", "Search audit logs for the consent event", "Search the unified audit log for 'Consent to application' events to identify the user, app ID, and time."),
            ("identification", "Assess data accessed by the app", "Use Defender XDR or Microsoft Defender for Cloud Apps to review app activity such as mail and file access."),
            ("containment", "Disable the enterprise application", "Set 'Enabled for users to sign in' to No for the app in Entra ID enterprise applications."),
            ("containment", "Revoke user sessions and tokens", "Revoke sign-in sessions for affected users to invalidate issued refresh tokens."),
            ("eradication", "Delete the app and its grants", "Remove the service principal and its delegated and application permission grants."),
            ("recovery", "Restore affected users", "Verify affected mailboxes and files are unchanged and re-enable normal access."),
            ("lessons_learned", "Restrict user consent", "Require admin consent workflow, limit user consent to verified publishers, and alert on new high-risk consents."),
        ],
    },
    {
        "name": "Risky Sign-in / Impossible Travel", "category": "identity",
        "description": "Sign-in flagged as risky because of anomalous location, device, or velocity.",
        "tasks": [
            ("identification", "Review the risk detection", "Open the risky sign-in in Entra ID Identity Protection or the Okta System Log and note risk level, IP, location, and device."),
            ("identification", "Validate with the user and context", "Check VPN or travel context, device compliance, and contact the user out of band to confirm the sign-in."),
            ("identification", "Check for follow-on activity", "Search the unified audit log for mailbox, file, or admin actions after the sign-in."),
            ("containment", "Revoke sessions if unverified", "Revoke sign-in sessions in Entra ID or clear sessions in Okta when the user does not confirm the activity."),
            ("eradication", "Reset credentials and MFA", "Force a password reset and reset MFA factors if the sign-in is confirmed malicious."),
            ("recovery", "Dismiss or confirm user risk", "Mark the user safe or confirm compromised in Identity Protection and restore access."),
            ("lessons_learned", "Tune risk policies", "Adjust named locations and conditional access risk policies to reduce false positives."),
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
