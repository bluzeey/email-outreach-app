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
            # Get draft (latest wins - order by created_at desc, id desc)
            draft_result = await session.execute(
                select(EmailDraft)
                .where(EmailDraft.campaign_row_id == row.id)
                .order_by(EmailDraft.created_at.desc(), EmailDraft.id.desc())
                .limit(1)
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
        
        # Delete ALL existing drafts for this row (safeguard against duplicates)
        result = await session.execute(
            select(EmailDraft).where(EmailDraft.campaign_row_id == row_id)
        )
        existing_drafts = result.scalars().all()
        for draft in existing_drafts:
            await session.delete(draft)
        
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


@router.post("/{campaign_id}/rows/{row_id}/retry")
async def retry_failed_row(
    campaign_id: str,
    row_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Retry sending for a failed row. If no draft exists, regenerate it first."""
    try:
        from app.api.campaigns import _send_recipient_email, _process_recipient_row
        from app.db.models import GmailAccount, EmailDraft

        row = await session.get(CampaignRow, row_id)
        if not row or row.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Row not found")

        # Only allow retry for failed rows
        if row.status != RowStatus.FAILED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot retry row with status: {row.status.value}. Only failed rows can be retried."
            )

        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # If campaign has no Gmail account associated, try to find and use the default one
        if not campaign.gmail_account_id:
            result = await session.execute(
                select(GmailAccount).where(GmailAccount.status == "active")
            )
            gmail_account = result.scalar_one_or_none()

            if not gmail_account:
                raise HTTPException(
                    status_code=400,
                    detail="No Gmail account connected. Please connect a Gmail account first."
                )

            # Associate the Gmail account with the campaign
            campaign.gmail_account_id = gmail_account.id
            logger.info(f"[RETRY] Auto-associated Gmail account {gmail_account.email} with campaign {campaign_id}")

        # Clear previous error
        row.error_message = None

        # Check if draft exists - if not, need to regenerate first
        draft_result = await session.execute(
            select(EmailDraft)
            .where(EmailDraft.campaign_row_id == row_id)
            .order_by(EmailDraft.created_at.desc())
            .limit(1)
        )
        draft = draft_result.scalar_one_or_none()

        if not draft:
            logger.info(f"[RETRY] No draft found for row {row_id}, regenerating first...")
            # Reset to QUEUED so _process_recipient_row will generate draft + send
            row.status = RowStatus.QUEUED
            await session.flush()

            try:
                await _process_recipient_row(session, campaign, row)
                await session.commit()

                if row.status == RowStatus.SENT:
                    return {
                        "success": True,
                        "row_id": row_id,
                        "message": "Draft regenerated and email sent successfully",
                        "new_status": row.status.value,
                    }
                else:
                    return {
                        "success": False,
                        "row_id": row_id,
                        "message": row.error_message or "Draft generation or send failed",
                        "new_status": row.status.value,
                    }
            except Exception as e:
                logger.error(f"[RETRY] Failed to process/regenerate row {row_id}: {e}")
                await session.rollback()
                row.status = RowStatus.FAILED
                row.error_message = f"Retry failed: {str(e)}"
                await session.commit()
                return {
                    "success": False,
                    "row_id": row_id,
                    "message": row.error_message,
                    "new_status": "failed",
                }

        # Draft exists - reset to GENERATED and send
        row.status = RowStatus.GENERATED
        await session.flush()

        logger.info(f"[RETRY] Retrying failed row {row_id} for campaign {campaign_id}")

        # Attempt to send the email again
        try:
            await _send_recipient_email(session, campaign, row)
            
            # Commit all changes (send_event record + row status)
            await session.commit()
            
            # If successful, row status should be SENT
            if row.status == RowStatus.SENT:
                logger.info(f"[RETRY] Successfully sent email to row {row_id}")
                return {
                    "success": True,
                    "row_id": row_id,
                    "message": "Email sent successfully",
                    "new_status": row.status.value,
                }
            else:
                # If still failed, return current status
                return {
                    "success": False,
                    "row_id": row_id,
                    "message": row.error_message or "Send failed",
                    "new_status": row.status.value,
                }
                
        except Exception as e:
            logger.error(f"[RETRY] Failed to resend row {row_id}: {e}")
            
            # Rollback the transaction to clear any partial state
            await session.rollback()
            
            # Start a fresh transaction to record the failure
            row.status = RowStatus.FAILED
            row.error_message = str(e)
            await session.commit()
            
            raise HTTPException(status_code=500, detail=f"Retry failed: {str(e)}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RETRY] Error retrying row {row_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Retry failed: {str(e)}")
