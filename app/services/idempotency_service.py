"""Idempotency service."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import generate_idempotency_key
from app.db.models import SendEvent, SendStatus

logger = get_logger(__name__)


class IdempotencyService:
    """Service for managing idempotency of email sends."""
    
    @staticmethod
    async def check_duplicate(
        session: AsyncSession,
        campaign_id: str,
        recipient_email: str,
        subject: str,
        body: str,
    ) -> SendEvent | None:
        """Check if this exact email was already sent."""
        key = generate_idempotency_key(campaign_id, recipient_email, subject, body)
        
        result = await session.execute(
            select(SendEvent).where(SendEvent.idempotency_key == key)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def record_send_attempt(
        session: AsyncSession,
        campaign_row_id: str,
        campaign_id: str,
        recipient_email: str,
        subject: str,
        body: str,
        status: SendStatus,
        provider_response: dict | None = None,
        error_message: str | None = None,
    ) -> SendEvent:
        """Record a send attempt."""
        key = generate_idempotency_key(campaign_id, recipient_email, subject, body)
        
        event = SendEvent(
            campaign_row_id=campaign_row_id,
            idempotency_key=key,
            provider="gmail",
            status=status,
            provider_response_json=provider_response or {},
            error_message=error_message,
        )
        
        session.add(event)
        await session.commit()
        
        logger.info(
            "Recorded send attempt",
            campaign_row_id=campaign_row_id,
            status=status.value,
            idempotency_key=key[:16] + "...",
        )
        
        return event
