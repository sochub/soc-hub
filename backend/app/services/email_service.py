"""Simple SMTP email sender for invitation emails.
Gracefully no-ops if SMTP is not configured.
"""
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
    tenant_name = tenant.name if tenant else "SOC Hub"

    subject = f"You've been invited to {tenant_name} on SOC Hub"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>You've been invited!</h2>
        <p>You've been invited to join <strong>{tenant_name}</strong> on SOC Hub.</p>
        <p>Click the link below to set up your account:</p>
        <p><a href="{invite_link}" style="display: inline-block; padding: 12px 24px; background-color: #2563eb; color: white; text-decoration: none; border-radius: 6px;">Accept Invitation</a></p>
        <p>Or copy this link: <br/><code>{invite_link}</code></p>
        <p style="color: #666; font-size: 12px;">This invitation expires in {settings.INVITATION_EXPIRE_HOURS} hours.</p>
    </body>
    </html>
    """

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
        logger.info("Invitation email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send invitation email to %s", to_email)
        return False
