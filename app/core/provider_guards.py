"""Server-side provider locks, independent of campaign or browser settings."""
from app.core.config import settings


class EmailSendingDisabled(RuntimeError):
    """An administrator has disabled real email delivery."""


class GmailAccessDisabled(RuntimeError):
    """Gmail has not been enabled for this installation."""


def require_email_sending() -> None:
    if not settings.EMAIL_SENDING_ENABLED:
        raise EmailSendingDisabled("Email sending is disabled. Dry-run previews remain available.")


def require_gmail_access() -> None:
    if not settings.GMAIL_CONNECTION_ENABLED:
        raise GmailAccessDisabled("Gmail connection is disabled until private setup is complete.")
