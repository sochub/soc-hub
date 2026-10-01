"""Simple SMTP email sender for invitation emails.
Gracefully no-ops if SMTP is not configured.
"""
import html
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            if settings.SMTP_PORT != 25:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Email sent to %s: %s", to_email, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


async def send_invitation_email(
    to_email: str,
    invite_link: str,
    tenant_id: int,
    db: AsyncSession,
) -> bool:
    """Send an invitation email. Returns True if sent, False if SMTP not configured."""
    if not _smtp_configured():
        logger.info("SMTP not configured — skipping invitation email for %s", to_email)
        return False

    # Get tenant name for email
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalars().first()
    tenant_name = html.escape(tenant.name if tenant else "SOC Hub")
    safe_link = html.escape(invite_link)

    subject = f"You've been invited to {tenant_name} on SOC Hub"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>You've been invited!</h2>
        <p>You've been invited to join <strong>{tenant_name}</strong> on SOC Hub.</p>
        <p>Click the link below to set up your account:</p>
        <p><a href="{safe_link}" style="display: inline-block; padding: 12px 24px; background-color: #2563eb; color: white; text-decoration: none; border-radius: 6px;">Accept Invitation</a></p>
        <p>Or copy this link: <br/><code>{safe_link}</code></p>
        <p style="color: #666; font-size: 12px;">This invitation expires in {settings.INVITATION_EXPIRE_HOURS} hours.</p>
    </body>
    </html>
    """

    return _send_email(to_email, subject, html_body)


async def send_sla_breach_email(to_email: str, case, breach_type: str) -> bool:
    """Notify one recipient that a case has just breached its response or
    resolution SLA. Returns True if sent, False if SMTP not configured."""
    if not _smtp_configured():
        logger.info("SMTP not configured — skipping SLA breach email for %s", to_email)
        return False

    label = "response" if breach_type == "response" else "resolution"
    safe_title = html.escape(case.title or "")
    severity = case.severity.value if hasattr(case.severity, "value") else str(case.severity)
    subject = f"SLA breach: case #{case.id} — {case.title}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #b91c1c;">SLA breach — {label} target missed</h2>
        <p>Case <strong>#{case.id}: {safe_title}</strong> (severity: {html.escape(severity)})
        has missed its {label} SLA target.</p>
        <p style="color: #666; font-size: 12px;">This is an automated notification from SOC Hub's SLA monitoring.</p>
    </body>
    </html>
    """
    return _send_email(to_email, subject, html_body)
