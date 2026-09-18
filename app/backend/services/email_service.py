"""
Email notification service for admin alerts.

Supports quota warnings, subscription expiry notices, and suspension
notifications.  Uses smtplib for delivery with graceful degradation
when SMTP is not configured.

Tenant-specific SMTP configuration is also supported — each tenant can
override the global SMTP settings with their own credentials, stored
encrypted in the ``tenant_email_configs`` table.
"""
import logging
import os
import smtplib
import ssl
from app.backend.services.reliability.timeouts import EMAIL_CONNECT, EMAIL_READ
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


# ── Encryption utilities ────────────────────────────────────────────────────

def _get_fernet():
    """Return a Fernet instance from the ENCRYPTION_KEY env var."""
    from cryptography.fernet import Fernet
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY environment variable not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_password(password: str) -> str:
    """Encrypt a plaintext password using Fernet symmetric encryption."""
    return _get_fernet().encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt a Fernet-encrypted password back to plaintext."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


class EmailService:
    """Sends admin notification emails via SMTP.

    Configuration is read from environment variables so no secrets are
    hard-coded.  When SMTP_HOST or SMTP_FROM are missing the service
    degrades gracefully — every public method returns ``False`` and
    logs a warning instead of raising.
    """

    def __init__(self) -> None:
        self.smtp_host: Optional[str] = os.getenv("SMTP_HOST")
        self.smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user: Optional[str] = os.getenv("SMTP_USER")
        self.smtp_password: Optional[str] = os.getenv("SMTP_PASSWORD")
        self.smtp_from: Optional[str] = os.getenv("SMTP_FROM")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """Return ``True`` when the minimum SMTP settings are present."""
        return bool(self.smtp_host and self.smtp_from)

    # ── Core send ─────────────────────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body_html: str) -> bool:
        """Send an HTML email to *to*.

        Returns ``True`` on success, ``False`` on failure (including
        the case where SMTP is not configured).
        """
        if not self.is_configured:
            logger.warning(
                "Email not sent — SMTP not configured (SMTP_HOST=%s, SMTP_FROM=%s)",
                self.smtp_host,
                self.smtp_from,
            )
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = self.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        try:
            with smtplib.SMTP(
                self.smtp_host, self.smtp_port, timeout=EMAIL_CONNECT,
            ) as server:
                server.sock.settimeout(EMAIL_READ)
                server.ehlo()
                if self.smtp_user and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_from, to, msg.as_string())
            logger.info("Email sent to %s: %s", to, subject)
            return True
        except Exception:
            logger.exception("Failed to send email to %s: %s", to, subject)
            return False

    # ── Template helpers ──────────────────────────────────────────────────────

    def send_quota_warning(
        self, tenant_name: str, admin_email: str, usage_pct: int
    ) -> bool:
        """Notify an admin that their tenant is approaching its quota."""
        body = (
            f"<h2>Quota Warning — {tenant_name}</h2>"
            f"<p>Your tenant <strong>{tenant_name}</strong> has reached "
            f"<strong>{usage_pct}%</strong> of its monthly analysis quota.</p>"
            f"<p>Please consider upgrading your plan or reducing usage to avoid "
            f"service interruptions.</p>"
            f"<hr><p style='color:gray;font-size:12px;'>"
            f"This is an automated message from ARIA Resume Intelligence.</p>"
        )
        return self.send_email(
            to=admin_email,
            subject=f"Quota Warning: {tenant_name} at {usage_pct}% usage",
            body_html=body,
        )

    def send_subscription_expiry(
        self, tenant_name: str, admin_email: str, days_remaining: int
    ) -> bool:
        """Notify an admin that their subscription is expiring soon."""
        body = (
            f"<h2>Subscription Expiring — {tenant_name}</h2>"
            f"<p>Your subscription for <strong>{tenant_name}</strong> expires in "
            f"<strong>{days_remaining} day{'s' if days_remaining != 1 else ''}</strong>.</p>"
            f"<p>Please renew your subscription to maintain uninterrupted access.</p>"
            f"<hr><p style='color:gray;font-size:12px;'>"
            f"This is an automated message from ARIA Resume Intelligence.</p>"
        )
        return self.send_email(
            to=admin_email,
            subject=f"Subscription Expiring: {tenant_name} — {days_remaining} day{'s' if days_remaining != 1 else ''} remaining",
            body_html=body,
        )

    def send_suspension_notice(
        self, tenant_name: str, admin_email: str, reason: str
    ) -> bool:
        """Notify an admin that their tenant has been suspended."""
        body = (
            f"<h2>Account Suspended — {tenant_name}</h2>"
            f"<p>Your tenant <strong>{tenant_name}</strong> has been suspended.</p>"
            f"<p><strong>Reason:</strong> {reason}</p>"
            f"<p>Please contact support if you believe this is an error.</p>"
            f"<hr><p style='color:gray;font-size:12px;'>"
            f"This is an automated message from ARIA Resume Intelligence.</p>"
        )
        return self.send_email(
            to=admin_email,
            subject=f"Account Suspended: {tenant_name}",
            body_html=body,
        )


# Module-level singleton for convenience
email_service = EmailService()


# ── Tenant-specific email service ──────────────────────────────────────────

class TenantEmailService:
    """Send emails using tenant-specific SMTP credentials.

    Unlike :class:`EmailService` which reads from env vars, this class
    is initialised with explicit SMTP parameters loaded from the
    ``tenant_email_configs`` table.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: Optional[str],
        smtp_password: Optional[str],
        smtp_from: str,
        from_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        encryption_type: str = "tls",
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from
        self.from_name = from_name
        self.reply_to = reply_to
        self.encryption_type = encryption_type  # tls | ssl | none

    @property
    def is_configured(self) -> bool:
        """Return ``True`` when the minimum SMTP settings are present."""
        return bool(self.smtp_host and self.smtp_from)

    def send_email(self, to: str, subject: str, body_html: str) -> bool:
        """Send an HTML email using the tenant's SMTP configuration.

        Returns ``True`` on success, ``False`` on failure.
        """
        if not self.is_configured:
            logger.warning(
                "Tenant email not sent — SMTP not configured (host=%s, from=%s)",
                self.smtp_host,
                self.smtp_from,
            )
            return False

        from_addr = self.smtp_from
        if self.from_name:
            from_addr = f"{self.from_name} <{self.smtp_from}>"

        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        if self.reply_to:
            msg["Reply-To"] = self.reply_to
        msg.attach(MIMEText(body_html, "html"))

        try:
            if self.encryption_type == "ssl":
                # SMTP_SSL — entire connection is TLS from the start
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(
                    self.smtp_host,
                    self.smtp_port,
                    context=context,
                    timeout=EMAIL_CONNECT,
                ) as server:
                    server.sock.settimeout(EMAIL_READ)
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_from, to, msg.as_string())
            elif self.encryption_type == "none":
                # Plain SMTP — no encryption at all
                with smtplib.SMTP(
                    self.smtp_host, self.smtp_port, timeout=EMAIL_CONNECT,
                ) as server:
                    server.sock.settimeout(EMAIL_READ)
                    server.ehlo()
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_from, to, msg.as_string())
            else:
                # Default: STARTTLS (upgrade plain connection to TLS)
                with smtplib.SMTP(
                    self.smtp_host, self.smtp_port, timeout=EMAIL_CONNECT,
                ) as server:
                    server.sock.settimeout(EMAIL_READ)
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_from, to, msg.as_string())

            logger.info("Tenant email sent to %s: %s", to, subject)
            return True
        except Exception:
            logger.exception("Failed to send tenant email to %s: %s", to, subject)
            return False


def get_tenant_email_service(db, tenant_id: int) -> Optional[TenantEmailService]:
    """Load tenant email config from DB, return TenantEmailService or ``None``.

    Returns ``None`` when the tenant has no active config, which signals
    the caller to fall back to the global :data:`email_service`.
    """
    from app.backend.models.db_models import TenantEmailConfig

    config = (
        db.query(TenantEmailConfig)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .first()
    )
    if config and config.smtp_host and config.smtp_password:
        try:
            password = decrypt_password(config.smtp_password)
            return TenantEmailService(
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
                smtp_user=config.smtp_user,
                smtp_password=password,
                smtp_from=config.smtp_from,
                from_name=config.from_name,
                reply_to=config.reply_to,
                encryption_type=config.encryption_type or "tls",
            )
        except Exception:
            logger.warning(
                "Failed to decrypt tenant %d email config — falling back to global",
                tenant_id,
                exc_info=True,
            )
    return None
