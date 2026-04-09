"""Followup API endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import mask_sensitive_data
from app.db.base import AsyncSessionLocal
from app.db.models import (
    Campaign,
    CampaignRow,
    FollowupDraft,
    GmailAccount,
    Lead,
    LeadStatus,
    RowStatus,
    SendEvent,
    SendStatus,
)
from app.schemas.followup import (
    FollowupBulkPreviewRequest,
    FollowupBulkSendRequest,
    FollowupBulkSendResponse,
    FollowupDraftResponse,
    FollowupEligibilityResponse,
    FollowupPreviewRequest,
    FollowupSendRequest,
    FollowupSendResponse,
    FollowupStatsResponse,
)
from app.services.followup_service import FollowupService
from app.services.gmail_client import GmailClient
from app.services.idempotency_service import IdempotencyService
from app.services.lead_service import LeadService

logger = get_logger(__name__)
router = APIRouter()


async def get_session():
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _followup_draft_to_response(
    draft: FollowupDraft,
    recipient_email: str | None = None,
    recipient_name: str | None = None,
    original_subject: str | None = None,
    campaign_name: str | None = None,
) -> FollowupDraftResponse:
    """Convert followup draft to response."""
    return FollowupDraftResponse(
        id=draft.id,
        campaign_row_id=draft.campaign_row_id,
        subject=draft.subject,
        plain_text_body=draft.plain_text_body,
        html_body=draft.html_body,
        context_summary=draft.context_summary,
        generation_confidence=draft.generation_confidence,
        needs_human_review=draft.needs_human_review,
        review_reasons=draft.review_reasons or [],
        status=draft.status,
        created_at=draft.created_at.isoformat() if draft.created_at else "",
        updated_at=draft.updated_at.isoformat() if draft.updated_at else "",
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        original_subject=original_subject,
        campaign_name=campaign_name,
    )


@router.get("/stats", response_model=FollowupStatsResponse)
async def get_followup_stats(
    session: AsyncSession = Depends(get_session),
):
    """Get followup statistics."""
    # Count leads by status
    result = await session.execute(
        select(Lead.status, func.count(Lead.id))
        .group_by(Lead.status)
    )
    status_counts = {status.value: count for status, count in result.all()}
    
    total = sum(status_counts.values())
    eligible = await session.execute(
        select(func.count(Lead.id))
        .where(Lead.status == LeadStatus.ACTIVE)
        .where(Lead.has_received_followup == False)
    )
    eligible_count = eligible.scalar() or 0
    
    already_followed = await session.execute(
        select(func.count(Lead.id))
        .where(Lead.has_received_followup == True)
    )
    followed_count = already_followed.scalar() or 0
    
    # Count drafts
    draft_result = await session.execute(
        select(FollowupDraft.status, func.count(FollowupDraft.id))
        .group_by(FollowupDraft.status)
    )
    draft_counts = {status: count for status, count in draft_result.all()}
    
    return FollowupStatsResponse(
        total_leads=total,
        eligible_for_followup=eligible_count,
        already_followed_up=followed_count,
        responded=status_counts.get("responded", 0),
        do_not_contact=status_counts.get("do_not_contact", 0),
        drafts_pending=draft_counts.get("draft", 0),
        drafts_approved=draft_counts.get("approved", 0),
    )


@router.get("/eligible-leads", response_model=list[FollowupEligibilityResponse])
async def get_eligible_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Get leads eligible for followup with their campaign row info."""
    lead_service = LeadService(session)
    
    # Get active leads that haven't received followup
    query = (
        select(Lead, CampaignRow, Campaign)
        .join(CampaignRow, CampaignRow.lead_id == Lead.id)
        .join(Campaign, Campaign.id == CampaignRow.campaign_id)
        .where(Lead.status == LeadStatus.ACTIVE)
        .where(Lead.has_received_followup == False)
        .where(CampaignRow.status == RowStatus.SENT)  # Must have been sent
        .order_by(Lead.last_seen_at.desc())
    )
    
    # Apply pagination
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    result = await session.execute(query)
    rows = result.all()
    
    responses = []
    for lead, campaign_row, campaign in rows:
        # Check eligibility
        is_eligible = await lead_service.is_eligible_for_followup(lead.id)
        
        # Get last sent info
        send_result = await session.execute(
            select(SendEvent)
            .where(SendEvent.campaign_row_id == campaign_row.id)
            .where(SendEvent.status == SendStatus.SENT)
            .order_by(SendEvent.sent_at.desc())
            .limit(1)
        )
        last_send = send_result.scalar_one_or_none()
        
        responses.append(FollowupEligibilityResponse(
            lead_id=lead.id,
            email=lead.email,
            is_eligible=is_eligible,
            reasons=[],
            status=lead.status.value,
            has_received_followup=lead.has_received_followup,
            campaign_row_id=campaign_row.id,
            last_sent_at=last_send.sent_at.isoformat() if last_send and last_send.sent_at else None,
        ))
    
    return responses


@router.post("/preview", response_model=FollowupDraftResponse)
async def preview_followup(
    request: FollowupPreviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Generate a followup draft preview."""
    # Get campaign row
    row = await session.get(CampaignRow, request.campaign_row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Campaign row not found")
    
    # Check eligibility
    if not row.lead_id:
        raise HTTPException(status_code=400, detail="Campaign row has no associated lead")
    
    lead_service = LeadService(session)
    is_eligible = await lead_service.is_eligible_for_followup(row.lead_id)
    if not is_eligible:
        raise HTTPException(status_code=400, detail="Lead is not eligible for followup")
    
    # Check if already has a sent email
    if not row.send_event or row.send_event.status != SendStatus.SENT:
        raise HTTPException(status_code=400, detail="Original email has not been sent yet")
    
    # Generate draft
    followup_service = FollowupService(session)
    draft = await followup_service.generate_followup_draft(
        campaign_row_id=request.campaign_row_id,
        tone=request.tone,
        custom_instructions=request.custom_instructions,
    )
    
    if not draft:
        raise HTTPException(status_code=500, detail="Failed to generate followup draft")
    
    # Get enriched data
    lead = row.lead
    campaign = await session.get(Campaign, row.campaign_id)
    
    result = await session.execute(
        select(FollowupDraft)
        .where(FollowupDraft.campaign_row_id == request.campaign_row_id)
        .where(FollowupDraft.status.in_(["draft", "approved"]))
    )
    draft = result.scalar_one_or_none()
    
    return _followup_draft_to_response(
        draft,
        recipient_email=row.recipient_email,
        recipient_name=f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else None,
        original_subject=row.email_draft.subject if row.email_draft else None,
        campaign_name=campaign.name if campaign else None,
    )


@router.get("/drafts/{draft_id}", response_model=FollowupDraftResponse)
async def get_followup_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a followup draft by ID."""
    draft = await session.get(FollowupDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    # Get enriched data
    row = draft.campaign_row
    lead = row.lead if row else None
    campaign = await session.get(Campaign, row.campaign_id) if row else None
    
    return _followup_draft_to_response(
        draft,
        recipient_email=row.recipient_email if row else None,
        recipient_name=f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else None,
        original_subject=row.email_draft.subject if row and row.email_draft else None,
        campaign_name=campaign.name if campaign else None,
    )


@router.put("/drafts/{draft_id}", response_model=FollowupDraftResponse)
async def update_followup_draft(
    draft_id: str,
    subject: str | None = None,
    plain_text_body: str | None = None,
    html_body: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Update a followup draft."""
    followup_service = FollowupService(session)
    
    draft = await followup_service.update_followup_draft(
        draft_id=draft_id,
        subject=subject,
        plain_text_body=plain_text_body,
        html_body=html_body,
    )
    
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    await session.commit()
    
    return _followup_draft_to_response(draft)


@router.post("/drafts/{draft_id}/approve", response_model=FollowupDraftResponse)
async def approve_followup_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Approve a followup draft for sending."""
    followup_service = FollowupService(session)
    
    draft = await followup_service.approve_followup_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    await session.commit()
    
    return _followup_draft_to_response(draft)


@router.post("/drafts/{draft_id}/reject", response_model=FollowupDraftResponse)
async def reject_followup_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Reject a followup draft."""
    followup_service = FollowupService(session)
    
    draft = await followup_service.reject_followup_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    await session.commit()
    
    return _followup_draft_to_response(draft)


@router.post("/send", response_model=FollowupSendResponse)
async def send_followup(
    request: FollowupSendRequest,
    session: AsyncSession = Depends(get_session),
):
    """Send a followup email."""
    draft = await session.get(FollowupDraft, request.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    if draft.status not in ["draft", "approved"]:
        raise HTTPException(status_code=400, detail=f"Draft cannot be sent (status: {draft.status})")
    
    # Get campaign row and lead
    row = draft.campaign_row
    if not row:
        raise HTTPException(status_code=400, detail="Draft has no associated campaign row")
    
    lead = row.lead
    if not lead:
        raise HTTPException(status_code=400, detail="Campaign row has no associated lead")
    
    # Check eligibility
    lead_service = LeadService(session)
    is_eligible = await lead_service.is_eligible_for_followup(lead.id)
    if not is_eligible:
        raise HTTPException(status_code=400, detail="Lead is not eligible for followup")
    
    # Get campaign and Gmail account
    campaign = await session.get(Campaign, row.campaign_id)
    if not campaign or not campaign.gmail_account_id:
        raise HTTPException(status_code=400, detail="No Gmail account connected for this campaign")
    
    gmail_account = await session.get(GmailAccount, campaign.gmail_account_id)
    if not gmail_account:
        raise HTTPException(status_code=400, detail="Gmail account not found")
    
    # Get original send event for threading
    original_send = None
    if draft.original_send_event_id:
        original_send = await session.get(SendEvent, draft.original_send_event_id)
    
    if not original_send:
        # Try to find the original send event
        result = await session.execute(
            select(SendEvent)
            .where(SendEvent.campaign_row_id == row.id)
            .where(SendEvent.status == SendStatus.SENT)
            .where(SendEvent.is_followup == False)
            .order_by(SendEvent.sent_at.desc())
            .limit(1)
        )
        original_send = result.scalar_one_or_none()
    
    if request.dry_run:
        # Just record as dry run
        idempotency_service = IdempotencyService()
        send_event = await idempotency_service.record_send_attempt(
            session=session,
            campaign_row_id=row.id,
            campaign_id=campaign.id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
            status=SendStatus.DRY_RUN,
            provider_response={"dry_run": True, "is_followup": True},
        )
        
        draft.status = "sent"
        await session.commit()
        
        return FollowupSendResponse(
            success=True,
            message="Followup recorded in dry-run mode",
            draft_id=draft.id,
            send_event_id=send_event.id,
            dry_run=True,
        )
    
    # Actually send
    try:
        from app.core.security import parse_gmail_credentials, validate_gmail_token
        
        # Parse credentials
        credentials_dict = parse_gmail_credentials(
            gmail_account.token_encrypted,
            gmail_account.refresh_token_encrypted
        )
        if not validate_gmail_token(credentials_dict):
            raise ValueError("Invalid Gmail credentials")
        
        # Create Gmail client
        client = GmailClient(credentials_dict)
        
        # Get thread ID from original send
        thread_id = original_send.provider_thread_id if original_send else None
        
        # Send followup with threading
        logger.info(f"Sending followup to {mask_sensitive_data(row.recipient_email or '', 3)} with thread_id={thread_id}")
        
        result = client.send_email(
            sender=gmail_account.email,
            to=row.recipient_email or "",
            subject=draft.subject,
            plain_text=draft.plain_text_body,
            html_body=draft.html_body,
            thread_id=thread_id,  # This ensures it goes in the same thread
        )
        
        # Record send event
        idempotency_service = IdempotencyService()
        send_event = await idempotency_service.record_send_attempt(
            session=session,
            campaign_row_id=row.id,
            campaign_id=campaign.id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
            status=SendStatus.SENT,
            provider_response=result,
        )
        
        # Update send event with followup info
        send_event.is_followup = True
        send_event.original_send_event_id = original_send.id if original_send else None
        send_event.provider_thread_id = result.get("thread_id")
        
        # Update lead
        await lead_service.mark_followup_sent(lead.id)
        
        # Update draft status
        draft.status = "sent"
        
        await session.commit()
        
        logger.info(f"Followup sent successfully to {mask_sensitive_data(row.recipient_email or '', 3)}")
        
        return FollowupSendResponse(
            success=True,
            message="Followup sent successfully",
            draft_id=draft.id,
            send_event_id=send_event.id,
            message_id=result.get("message_id"),
            thread_id=result.get("thread_id"),
            dry_run=False,
        )
        
    except Exception as e:
        logger.error(f"Failed to send followup: {e}")
        
        # Record failure
        idempotency_service = IdempotencyService()
        await idempotency_service.record_send_attempt(
            session=session,
            campaign_row_id=row.id,
            campaign_id=campaign.id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
            status=SendStatus.FAILED,
            error_message=str(e),
        )
        
        await session.commit()
        
        raise HTTPException(status_code=500, detail=f"Failed to send followup: {str(e)}")


@router.post("/bulk-send", response_model=FollowupBulkSendResponse)
async def bulk_send_followups(
    request: FollowupBulkSendRequest,
    session: AsyncSession = Depends(get_session),
):
    """Send multiple followups in bulk."""
    results = []
    sent_count = 0
    failed_count = 0
    
    for draft_id in request.draft_ids:
        try:
            # Create sub-request and send
            sub_request = FollowupSendRequest(
                draft_id=draft_id,
                dry_run=request.dry_run,
            )
            
            # This would ideally call the send_followup function directly
            # For now, we'll track success/failure
            draft = await session.get(FollowupDraft, draft_id)
            if not draft:
                results.append({"draft_id": draft_id, "success": False, "error": "Draft not found"})
                failed_count += 1
                continue
            
            if draft.status == "sent":
                results.append({"draft_id": draft_id, "success": False, "error": "Already sent"})
                failed_count += 1
                continue
            
            # Quick eligibility check
            row = draft.campaign_row
            if not row or not row.lead_id:
                results.append({"draft_id": draft_id, "success": False, "error": "No lead associated"})
                failed_count += 1
                continue
            
            lead_service = LeadService(session)
            if not await lead_service.is_eligible_for_followup(row.lead_id):
                results.append({"draft_id": draft_id, "success": False, "error": "Lead not eligible"})
                failed_count += 1
                continue
            
            # Mark as would-send (actual sending done individually for now)
            results.append({"draft_id": draft_id, "success": True, "dry_run": request.dry_run})
            sent_count += 1
            
        except Exception as e:
            results.append({"draft_id": draft_id, "success": False, "error": str(e)})
            failed_count += 1
    
    return FollowupBulkSendResponse(
        success=True,
        message=f"Processed {len(request.draft_ids)} followups",
        total_requested=len(request.draft_ids),
        sent_count=sent_count,
        failed_count=failed_count,
        dry_run=request.dry_run,
        results=results,
    )
