"""Review API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import AsyncSessionLocal
from app.db.models import (
    ApprovalDecision,
    ApprovalEvent,
    Campaign,
    CampaignRow,
    EmailDraft,
    RowStatus,
)
from app.graphs.recipient_graph import create_recipient_graph, get_recipient_thread_id
from app.graphs.state import RecipientGraphState
from app.schemas.draft import SampleDraftsResponse
from app.schemas.review import (
    CampaignReviewResponse,
    RowReviewRequest,
    RowReviewResponse,
)

logger = get_logger(__name__)
router = APIRouter()


async def get_session():
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@router.get("/{campaign_id}/samples", response_model=SampleDraftsResponse)
async def get_sample_drafts(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get sample drafts for campaign review."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Get first few rows with drafts
        result = await session.execute(
            select(CampaignRow)
            .where(CampaignRow.campaign_id == campaign_id)
            .order_by(CampaignRow.row_number)
            .limit(5)
        )
        rows = result.scalars().all()
        
        drafts = []
        for row in rows:
            draft_result = await session.execute(
                select(EmailDraft).where(EmailDraft.campaign_row_id == row.id)
            )
            draft = draft_result.scalar_one_or_none()
            
            if draft:
                drafts.append({
                    "row_number": row.row_number,
                    "recipient_email": row.recipient_email,
                    "subject": draft.subject,
                    "plain_text_body": draft.plain_text_body,
                    "html_body": draft.html_body,
                    "personalization_fields_used": draft.personalization_fields_used,
                    "needs_human_review": draft.needs_human_review,
                    "review_reasons": draft.review_reasons,
                })
        
        return SampleDraftsResponse(
            campaign_id=campaign_id,
            drafts=drafts,
            total_samples=len(drafts),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get samples: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get samples: {str(e)}")


@router.post("/{campaign_id}/rows/{row_id}/approve", response_model=RowReviewResponse)
async def approve_row(
    campaign_id: str,
    row_id: str,
    request: RowReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Approve a row for sending."""
    try:
        row = await session.get(CampaignRow, row_id)
        if not row or row.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Row not found")
        
        # Record approval
        approval = ApprovalEvent(
            campaign_id=campaign_id,
            campaign_row_id=row_id,
            decision=ApprovalDecision.APPROVED,
            reviewer="operator",
            notes=request.notes,
        )
        session.add(approval)
        
        # Update row status
        row.status = RowStatus.SENDING
        await session.commit()
        
        # Resume recipient graph
        graph = create_recipient_graph(session)
        thread_id = get_recipient_thread_id(campaign_id, row_id)
        
        # Continue processing with approval
        result = await graph.ainvoke(
            None,  # Continue from checkpoint
            config={"configurable": {"thread_id": thread_id}},
        )
        
        return RowReviewResponse(
            row_id=row_id,
            decision="approved",
            new_status=row.status.value,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to approve row: {e}")
        raise HTTPException(status_code=500, detail=f"Approval failed: {str(e)}")


@router.post("/{campaign_id}/rows/{row_id}/reject", response_model=RowReviewResponse)
async def reject_row(
    campaign_id: str,
    row_id: str,
    request: RowReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Reject a row (skip sending)."""
    try:
        row = await session.get(CampaignRow, row_id)
        if not row or row.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Row not found")
        
        # Record rejection
        approval = ApprovalEvent(
            campaign_id=campaign_id,
            campaign_row_id=row_id,
            decision=ApprovalDecision.REJECTED,
            reviewer="operator",
            notes=request.notes,
        )
        session.add(approval)
        
        # Update row status
        row.status = RowStatus.SKIPPED
        await session.commit()
        
        return RowReviewResponse(
            row_id=row_id,
            decision="rejected",
            new_status=row.status.value,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject row: {e}")
        raise HTTPException(status_code=500, detail=f"Rejection failed: {str(e)}")


@router.post("/{campaign_id}/rows/{row_id}/regenerate")
async def regenerate_row_draft(
    campaign_id: str,
    row_id: str,
    instructions: str = None,
    session: AsyncSession = Depends(get_session),
):
    """Regenerate draft for a row."""
    try:
        row = await session.get(CampaignRow, row_id)
        if not row or row.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Row not found")
        
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Delete existing draft
        result = await session.execute(
            select(EmailDraft).where(EmailDraft.campaign_row_id == row_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
        
        # Reset row status
        row.status = RowStatus.QUEUED
        await session.commit()
        
        # Re-run recipient graph
        graph = create_recipient_graph(session)
        thread_id = get_recipient_thread_id(campaign_id, row_id)
        
        # Clear checkpoint and re-run
        initial_state = RecipientGraphState(
            campaign_id=campaign_id,
            recipient_id=row_id,
            row_number=row.row_number,
            raw_row=row.raw_row_json,
            dry_run=campaign.dry_run,
        )
        
        result = await graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
        
        return {
            "success": True,
            "row_id": row_id,
            "message": "Draft regenerated",
            "new_status": result.get("status"),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to regenerate row: {e}")
        raise HTTPException(status_code=500, detail=f"Regeneration failed: {str(e)}")
