"""Send service for managing email sending."""

from datetime import datetime
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import generate_idempotency_key, mask_sensitive_data
from app.db.models import CampaignRow, EmailDraft, RowStatus, SendEvent, SendStatus
from app.services.gmail_client import GmailClient

logger = get_logger(__name__)


class SendService:
    """Service for sending emails with idempotency and retries."""
    
    def __init__(self):
        self.dry_run = settings.DRY_RUN_DEFAULT
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def send_email(
        self,
        gmail_client: GmailClient,
        sender_email: str,
        row: CampaignRow,
        draft: EmailDraft,
        dry_run: bool = None,
    ) -> dict:
        """Send email with idempotency check."""
        
        if dry_run is None:
            dry_run = self.dry_run
        
        # Generate idempotency key
        idempotency_key = generate_idempotency_key(
            campaign_id=row.campaign_id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
        )
        
        logger.info(
            "Processing send request",
            campaign_id=row.campaign_id,
            row_id=row.id,
            recipient=mask_sensitive_data(row.recipient_email or "", 3),
            dry_run=dry_run,
        )
        
        # In dry-run mode, just record as preview
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "idempotency_key": idempotency_key,
                "message": "Email recorded in dry-run mode (not actually sent)",
            }
        
        # Actually send via Gmail
        try:
            result = gmail_client.send_email(
                sender=sender_email,
                to=row.recipient_email,
                subject=draft.subject,
                plain_text=draft.plain_text_body,
                html_body=draft.html_body,
            )
            
            logger.info(
                "Email sent successfully",
                message_id=result.get("message_id"),
                recipient=mask_sensitive_data(row.recipient_email or "", 3),
            )
            
            return {
                "success": True,
                "dry_run": False,
                "idempotency_key": idempotency_key,
                "message_id": result.get("message_id"),
                "provider_response": result.get("raw_response"),
            }
            
        except Exception as e:
            logger.error(
                "Failed to send email",
                error=str(e),
                recipient=mask_sensitive_data(row.recipient_email or "", 3),
            )
            raise
    
    def check_duplicate(
        self,
        existing_events: list[SendEvent],
        idempotency_key: str,
    ) -> SendEvent | None:
        """Check if email was already sent."""
        for event in existing_events:
            if event.idempotency_key == idempotency_key:
                return event
        return None
