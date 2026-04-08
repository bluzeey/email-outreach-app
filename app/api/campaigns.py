"""Campaign API endpoints."""

import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import List

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import AsyncSessionLocal
from app.db.models import (
    Campaign,
    CampaignRow,
    CampaignStatus,
    GmailAccount,
    RowStatus,
)
from app.graphs.campaign_graph import create_campaign_graph, get_campaign_thread_id
from app.graphs.recipient_graph import create_recipient_graph, get_recipient_thread_id
from app.graphs.state import CampaignGraphState, RecipientGraphState
from app.schemas.campaign import (
    CampaignActionResponse,
    CampaignAnalyzeResponse,
    CampaignApproveRequest,
    CampaignCreateRequest,
    CampaignExportResponse,
    CampaignListResponse,
    CampaignPlanUpdateRequest,
    CampaignProgressResponse,
    CampaignRegenerateDraftsResponse,
    CampaignResponse,
    CampaignUploadResponse,
)
from app.schemas.recipient import (
    EmailDraftResponse,
    EmailDraftUpdateRequest,
    RecipientListResponse,
    RecipientRowResponse,
)
from app.services.csv_loader import CSVLoader, DataLoader
from app.services.csv_profiler import CSVProfiler
from app.services.progress_manager import progress_manager

logger = get_logger(__name__)
router = APIRouter()

# Global lock for campaign analysis (SQLite can only handle one writer at a time)
_analysis_lock = asyncio.Lock()
_analyzing_campaigns: set[str] = set()


async def get_session():
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _campaign_to_response(campaign: Campaign) -> CampaignResponse:
    """Convert campaign model to response schema."""
    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        context=campaign.context,
        status=campaign.status.value,
        dry_run=campaign.dry_run,
        csv_filename=campaign.csv_filename,
        inferred_schema_json=campaign.inferred_schema_json or {},
        campaign_plan_json=campaign.campaign_plan_json or {},
        sample_drafts_json=campaign.sample_drafts_json or [],
        totals_json=campaign.totals_json or {},
        dispatch_cursor=0,  # Would be tracked separately
        created_at=campaign.created_at.isoformat() if campaign.created_at else "",
        updated_at=campaign.updated_at.isoformat() if campaign.updated_at else "",
        errors=campaign.errors or [],
    )


import asyncio
from functools import wraps


def with_retry(max_attempts=3, delay=0.5):
    """Decorator for retrying database operations with exponential backoff."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    # Only retry on database lock errors
                    if "database is locked" in error_str or "busy" in error_str:
                        if attempt < max_attempts:
                            wait_time = delay * (2 ** (attempt - 1))  # Exponential backoff
                            logger.warning(f"Database locked, retrying in {wait_time}s (attempt {attempt}/{max_attempts})")
                            await asyncio.sleep(wait_time)
                            continue
                    raise
            raise last_exception
        return wrapper
    return decorator


@router.post("", response_model=CampaignResponse)
@with_retry(max_attempts=5, delay=0.5)
async def create_campaign(
    request: CampaignCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new campaign."""
    try:
        campaign = Campaign(
            name=request.name,
            context=request.context,
            dry_run=request.dry_run,
            status=CampaignStatus.CREATED,
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        
        logger.info(f"Created campaign: {campaign.id}")
        return _campaign_to_response(campaign)
        
    except Exception as e:
        logger.error(f"Failed to create campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")


@router.post("/{campaign_id}/upload", response_model=CampaignUploadResponse)
async def upload_file(
    campaign_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Upload CSV or Excel file for a campaign.

    Supports two modes:
    1. Initial upload: Campaign has no rows yet (new campaign)
    2. Append upload: Campaign already has rows + inferred schema - adds only new leads
    """
    try:
        # Check campaign exists
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Validate file type
        if not DataLoader.is_supported_file(file.filename):
            raise HTTPException(
                status_code=400,
                detail="Only CSV and Excel files (.csv, .xls, .xlsx, .xlsm) are allowed"
            )

        # Get file extension
        file_ext = DataLoader.get_file_extension(file.filename)

        # Save file
        file_id = str(uuid.uuid4())
        dest_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}.{file_ext}")

        await DataLoader.save_upload(file, dest_path)

        # Load and profile data
        df = DataLoader.load_file(dest_path)

        # Check if this is an append operation (campaign has schema + existing rows)
        existing_rows_result = await session.execute(
            select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        )
        existing_rows = existing_rows_result.scalars().all()

        has_existing_schema = bool(campaign.inferred_schema_json and campaign.inferred_schema_json.get("primary_email_column"))

        if existing_rows and has_existing_schema:
            # APPEND MODE: Add only new leads
            return await _append_leads_to_campaign(
                campaign, df, dest_path, file.filename, session
            )
        else:
            # INITIAL MODE: Standard upload for new campaign
            campaign.csv_filename = file.filename
            campaign.csv_storage_path = dest_path
            campaign.status = CampaignStatus.PROFILING
            await session.commit()

            logger.info(f"Initial upload for campaign {campaign_id}: {len(df)} rows")

            return CampaignUploadResponse(
                campaign_id=campaign_id,
                filename=file.filename,
                row_count=len(df),
                columns=list(df.columns),
                mode="initial",
            )

    except HTTPException:
        raise
    except ImportError as e:
        logger.error(f"Missing Excel dependencies: {e}")
        raise HTTPException(
            status_code=500,
            detail="Excel support not available. Please install openpyxl and xlrd."
        )
    except Exception as e:
        logger.error(f"Failed to upload file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")


async def _append_leads_to_campaign(
    campaign: Campaign,
    df: "pd.DataFrame",
    dest_path: str,
    filename: str,
    session: AsyncSession,
) -> CampaignUploadResponse:
    """Append new leads to an existing campaign with inferred schema.

    - Skip duplicate emails (already in campaign or duplicates in new file)
    - Skip invalid/placeholder emails
    - Create rows with QUEUED status so they can be processed
    - If campaign was COMPLETED, move to PAUSED so user can Run again
    """
    campaign_id = campaign.id
    schema = campaign.inferred_schema_json or {}
    email_col = schema.get("primary_email_column", "")

    if not email_col or email_col not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"Email column '{email_col}' not found in uploaded file. Expected schema: {list(schema.keys())}"
        )

    # Get existing emails from campaign (case-insensitive, stripped)
    existing_result = await session.execute(
        select(CampaignRow.recipient_email).where(CampaignRow.campaign_id == campaign_id)
    )
    existing_emails = set()
    for (email,) in existing_result.all():
        if email:
            existing_emails.add(email.lower().strip())

    # Get max row number for continuing sequence
    max_row_result = await session.execute(
        select(CampaignRow.row_number)
        .where(CampaignRow.campaign_id == campaign_id)
        .order_by(CampaignRow.row_number.desc())
        .limit(1)
    )
    max_row = max_row_result.scalar() or 0
    next_row_number = max_row + 1

    # Invalid/placeholder values to skip
    invalid_placeholders = {"", "nan", "null", "none", "n/a", "na", "-", "--", "..."}

    added_count = 0
    skipped_duplicates = 0
    skipped_invalid = 0
    seen_in_file = set()  # Track duplicates within the same file

    from app.services.draft_generation_service import DraftGenerationService

    draft_service = DraftGenerationService()
    schema_obj = None
    plan_obj = None

    # Parse schema and plan objects for draft generation
    try:
        from app.schemas.csv_inference import CsvSchemaInference, CampaignPlan
        schema_obj = CsvSchemaInference(**schema)
        plan_obj = CampaignPlan(**(campaign.campaign_plan_json or {}))
    except Exception as e:
        logger.warning(f"Could not parse schema/plan for draft generation: {e}")

    # Get sender info for drafts
    sender_name = None
    signature = None
    if campaign.gmail_account_id:
        from app.db.models import GmailAccount
        gmail_account = await session.get(GmailAccount, campaign.gmail_account_id)
        if gmail_account:
            sender_name = gmail_account.sender_name
            signature = gmail_account.signature

    # Process each row
    for idx, row_data in df.iterrows():
        # Get email from row
        email_raw = str(row_data.get(email_col, "")).strip()
        email_lower = email_raw.lower()

        # Check for invalid/placeholder emails
        if not email_raw or email_lower in invalid_placeholders or pd.isna(row_data.get(email_col)):
            skipped_invalid += 1
            continue

        # Check for duplicates in existing campaign
        if email_lower in existing_emails:
            skipped_duplicates += 1
            continue

        # Check for duplicates within the same file
        if email_lower in seen_in_file:
            skipped_duplicates += 1
            continue

        seen_in_file.add(email_lower)

        # Prepare row data
        row_dict = row_data.to_dict()

        # Create CampaignRow with QUEUED status
        try:
            campaign_row = CampaignRow(
                campaign_id=campaign_id,
                row_number=next_row_number,
                raw_row_json=row_dict,
                recipient_email=email_raw,
                status=RowStatus.QUEUED,
            )
            session.add(campaign_row)
            await session.flush()  # Get the row ID

            # Generate draft immediately for the new row
            if schema_obj and plan_obj:
                try:
                    draft = await draft_service.generate_draft(
                        schema_obj, plan_obj, row_dict, sender_name, signature
                    )

                    # Save draft to DB
                    from app.db.models import EmailDraft
                    email_draft = EmailDraft(
                        campaign_row_id=campaign_row.id,
                        subject=draft.subject,
                        plain_text_body=draft.plain_text_body,
                        html_body=draft.html_body,
                        personalization_fields_used=draft.personalization_fields_used,
                        key_claims_used=draft.key_claims_used,
                        generation_confidence=int(draft.confidence * 100),
                        needs_human_review=draft.needs_human_review,
                        review_reasons=draft.review_reasons,
                    )
                    session.add(email_draft)

                    # Update row status to GENERATED (ready to send)
                    campaign_row.status = RowStatus.GENERATED

                except Exception as draft_error:
                    logger.error(f"Failed to generate draft for appended row {campaign_row.id}: {draft_error}")
                    # Keep as QUEUED - will be generated during run

            added_count += 1
            next_row_number += 1

            # Commit every 10 rows to avoid memory issues
            if added_count % 10 == 0:
                await session.commit()

        except Exception as row_error:
            logger.error(f"Failed to create row for email {email_raw}: {row_error}")
            # Continue with next row
            continue

    # Final commit for remaining rows
    await session.commit()

    # Update campaign totals
    total_rows_result = await session.execute(
        select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
    )
    total_rows = len(total_rows_result.scalars().all())

    campaign.totals_json = campaign.totals_json or {}
    campaign.totals_json["total_rows"] = total_rows

    # If campaign was COMPLETED, move to PAUSED so user can Run again
    if campaign.status == CampaignStatus.COMPLETED:
        campaign.status = CampaignStatus.PAUSED
        logger.info(f"Campaign {campaign_id} moved from COMPLETED to PAUSED after adding {added_count} new leads")

    # Update file reference (track the latest upload)
    campaign.csv_filename = filename
    campaign.csv_storage_path = dest_path

    await session.commit()

    logger.info(
        f"Appended leads to campaign {campaign_id}: "
        f"{added_count} added, {skipped_duplicates} duplicates skipped, "
        f"{skipped_invalid} invalid skipped. Total rows now: {total_rows}"
    )

    return CampaignUploadResponse(
        campaign_id=campaign_id,
        filename=filename,
        row_count=len(df),
        columns=list(df.columns),
        added_rows=added_count,
        skipped_duplicates=skipped_duplicates,
        skipped_invalid=skipped_invalid,
        mode="append",
    )


# Keep old endpoint for backwards compatibility
@router.post("/{campaign_id}/upload-csv", response_model=CampaignUploadResponse)
async def upload_csv(
    campaign_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Upload CSV file for a campaign (deprecated, use /upload instead)."""
    return await upload_file(campaign_id, file, session)


@router.post("/{campaign_id}/analyze", response_model=CampaignAnalyzeResponse)
async def analyze_campaign(
    campaign_id: str,
    force_restart: bool = False,  # Optional param to restart from scratch
    session: AsyncSession = Depends(get_session),
):
    """Analyze campaign CSV and generate schema/plan.
    
    Supports resuming from checkpoint if analysis was interrupted.
    Set force_restart=True to start fresh and delete existing progress.
    
    Note: SQLite only supports one writer at a time, so analysis is serialized
    globally across all campaigns.
    """
    # Check if this specific campaign is already being analyzed
    if campaign_id in _analyzing_campaigns:
        raise HTTPException(
            status_code=423,  # Locked
            detail="Analysis is already running for this campaign. Please wait for it to complete."
        )
    
    # Check if any campaign is being analyzed (SQLite global lock)
    if _analyzing_campaigns:
        raise HTTPException(
            status_code=423,  # Locked
            detail=f"Another campaign is currently being analyzed. Only one analysis can run at a time with SQLite. Currently analyzing: {list(_analyzing_campaigns)[0]}"
        )
    
    # Add this campaign to the analyzing set
    _analyzing_campaigns.add(campaign_id)
    
    try:
        # Acquire global lock for analysis (SQLite serialization)
        async with _analysis_lock:
            return await _run_analysis(campaign_id, force_restart, session)
    finally:
        # Always clean up the tracking set
        _analyzing_campaigns.discard(campaign_id)


async def _run_analysis(
    campaign_id: str,
    force_restart: bool,
    session: AsyncSession,
) -> CampaignAnalyzeResponse:
    """Internal analysis function that runs under the global lock."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        if not campaign.csv_storage_path:
            raise HTTPException(status_code=400, detail="No CSV uploaded")
        
        # Check for existing recipient rows
        existing_rows_result = await session.execute(
            select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        )
        existing_rows = existing_rows_result.scalars().all()
        
        if existing_rows and force_restart:
            # Delete existing rows if force restart
            logger.info(f"Force restart requested, deleting {len(existing_rows)} existing rows")
            for row in existing_rows:
                await session.delete(row)
            await session.commit()
            existing_rows = []
        
        # Start progress tracking
        if existing_rows:
            await progress_manager.update(
                campaign_id,
                status="resuming",
                message=f"Resuming analysis with {len(existing_rows)} existing recipients...",
                stage="resuming",
                total_rows=0,
                processed_rows=len(existing_rows),
            )
        else:
            await progress_manager.update(
                campaign_id,
                status="starting",
                message="Loading and profiling CSV...",
                stage="loading",
                total_rows=0,
                processed_rows=0,
            )
        
        # Create a fresh session for graph operations to avoid SQLite locking
        async with AsyncSessionLocal() as graph_session:
            try:
                # Create graph with fresh session
                graph = create_campaign_graph(graph_session)
                thread_id = get_campaign_thread_id(campaign_id)
                
                # Build initial state
                initial_state = CampaignGraphState(
                    campaign_id=campaign_id,
                    context=campaign.context or "",
                    csv_path=campaign.csv_storage_path,
                    dry_run=campaign.dry_run,
                )
                
                # If we have existing rows and not force restart, 
                # we'll skip to await_approval status
                if existing_rows and not force_restart:
                    logger.info(f"Resuming campaign {campaign_id} with {len(existing_rows)} existing rows")
                    
                    # Set up state as if we completed prepare_recipients
                    initial_state.inferred_schema = campaign.inferred_schema_json or {}
                    initial_state.campaign_plan = campaign.campaign_plan_json or {}
                    initial_state.sample_drafts = campaign.sample_drafts_json or []
                    initial_state.row_ids = [row.id for row in existing_rows]
                    initial_state.totals = campaign.totals_json or {
                        "total_rows": len(existing_rows),
                        "processed": 0,
                        "sent": 0,
                        "failed": 0,
                        "skipped": 0,
                    }
                    initial_state.status = "awaiting_campaign_approval"
                    
                    await progress_manager.update(
                        campaign_id,
                        status="complete",
                        message=f"Resumed with {len(existing_rows)} recipients - analysis complete!",
                        stage="complete",
                        percent_complete=100,
                    )
                    
                    result = initial_state.model_dump()
                else:
                    # Fresh start - run full graph
                    result = await graph.ainvoke(
                        initial_state,
                        config={"configurable": {"thread_id": thread_id}},
                    )
                    
                    # Commit all graph operations
                    await graph_session.commit()
                    
                    # Mark as complete
                    await progress_manager.update(
                        campaign_id,
                        status="complete",
                        message="Analysis complete!",
                        stage="complete",
                        percent_complete=100,
                    )
                
            except Exception as e:
                await graph_session.rollback()
                await progress_manager.update(
                    campaign_id,
                    status="error",
                    message=f"Error: {str(e)}",
                    stage="error",
                )
                raise e
        
        # Update campaign status in the original session
        campaign.status = CampaignStatus(result.get("status", "awaiting_schema_review"))
        campaign.inferred_schema_json = result.get("inferred_schema", {})
        campaign.campaign_plan_json = result.get("campaign_plan", {})
        campaign.sample_drafts_json = result.get("sample_drafts", [])
        campaign.totals_json = result.get("totals", {})
        
        # Clear recovery error messages if present
        if campaign.errors:
            campaign.errors = [
                e for e in campaign.errors 
                if "Analysis was interrupted" not in e
            ]
        
        await session.commit()
        
        return CampaignAnalyzeResponse(
            campaign_id=campaign_id,
            schema_inference=result.get("inferred_schema", {}),
            campaign_plan=result.get("campaign_plan", {}),
            sample_count=len(result.get("sample_drafts", [])),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to analyze campaign {campaign_id}: {e}")
        # Ensure session is rolled back on error
        try:
            await session.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/{campaign_id}/progress-stream")
async def progress_stream(campaign_id: str):
    """Server-Sent Events endpoint for real-time progress updates."""
    
    async def event_generator():
        # Register this campaign for progress tracking
        queue = progress_manager.register(campaign_id)
        
        try:
            while True:
                # Wait for next event (with timeout to prevent connection from hanging)
                try:
                    event = await asyncio.wait_for(
                        progress_manager.get_event(campaign_id),
                        timeout=30.0
                    )
                    if event:
                        yield f"data: {event}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield ":ping\n\n"
                    
        except Exception as e:
            logger.error(f"SSE error for campaign {campaign_id}: {e}")
        finally:
            progress_manager.unregister(campaign_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.post("/{campaign_id}/approve", response_model=CampaignActionResponse)
async def approve_campaign(
    campaign_id: str,
    request: CampaignApproveRequest = None,
    session: AsyncSession = Depends(get_session),
):
    """Approve campaign to start processing."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        if campaign.status not in [CampaignStatus.AWAITING_CAMPAIGN_APPROVAL, CampaignStatus.AWAITING_SCHEMA_REVIEW]:
            raise HTTPException(status_code=400, detail=f"Campaign cannot be approved in status: {campaign.status.value}")
        
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
            logger.info(f"[APPROVE] Auto-associated Gmail account {gmail_account.email} with campaign {campaign_id}")
        
        # Get the Gmail account
        gmail_account = await session.get(GmailAccount, campaign.gmail_account_id)
        if not gmail_account:
            raise HTTPException(
                status_code=400,
                detail="Gmail account not found. Please reconnect your Gmail account."
            )
        
        # FAIL-FAST: Validate Gmail credentials before processing any rows
        if not gmail_account.token_encrypted:
            raise HTTPException(
                status_code=401,
                detail="Gmail authentication token not found. Please go to Settings and reconnect your Gmail account."
            )
        
        # Validate token can be decrypted and parsed
        from app.core.security import parse_gmail_credentials, validate_gmail_token
        try:
            credentials_dict = parse_gmail_credentials(
                gmail_account.token_encrypted,
                gmail_account.refresh_token_encrypted
            )
            if not validate_gmail_token(credentials_dict):
                raise ValueError("Invalid credentials structure")
            
            # Test that we can create a Gmail client
            from app.services.gmail_client import GmailClient
            test_client = GmailClient(credentials_dict)
            logger.info(f"[APPROVE] Gmail credentials validated successfully for {gmail_account.email}")
        except Exception as e:
            logger.error(f"[APPROVE] Gmail token validation failed: {e}")
            # Auto-disconnect the corrupted account
            gmail_account.status = "disconnected"
            await session.commit()
            raise HTTPException(
                status_code=401,
                detail="Gmail authentication token is corrupted or expired. Please go to Settings and reconnect your Gmail account."
            ) from e
        
        # Update campaign status to RUNNING
        campaign.status = CampaignStatus.RUNNING
        await session.commit()
        
        logger.info(f"Campaign {campaign_id} approved, starting recipient processing")
        
        # Get all rows with generated drafts and send them
        result = await session.execute(
            select(CampaignRow).where(
                CampaignRow.campaign_id == campaign_id,
                CampaignRow.status.in_([RowStatus.GENERATED, RowStatus.QUEUED, RowStatus.NORMALIZED])
            )
        )
        pending_rows = result.scalars().all()
        
        logger.info(f"Found {len(pending_rows)} rows with drafts ready to send")
        
        # Send each email (drafts are already generated)
        # Commit after each row for real-time status updates in the UI
        processed = 0
        failed = 0
        for row in pending_rows:
            try:
                await _send_recipient_email(session, campaign, row)
                processed += 1
                if row.status == RowStatus.FAILED:
                    failed += 1
                # Commit per row so UI can see real-time status
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to send email to row {row.id}: {e}")
                row.status = RowStatus.FAILED
                row.error_message = str(e)
                failed += 1
                await session.commit()

        # Final refresh of totals
        
        # Check if all rows are processed
        total_result = await session.execute(
            select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        )
        total_rows = len(total_result.scalars().all())
        
        # Count actual sent and skipped (not failed)
        successful_rows = sum(1 for r in pending_rows if r.status == RowStatus.SENT)
        skipped_rows = sum(1 for r in pending_rows if r.status == RowStatus.SKIPPED)
        
        # Only mark COMPLETED when all rows are successfully sent or skipped
        # If there are failures, keep campaign as RUNNING so user can retry
        if successful_rows + skipped_rows >= total_rows:
            campaign.status = CampaignStatus.COMPLETED
            await session.commit()
        elif failed > 0:
            # Keep as RUNNING if there are failures (allows retry)
            campaign.status = CampaignStatus.RUNNING
            await session.commit()
        
        return CampaignActionResponse(
            success=True,
            message=f"Campaign approved! Processed {processed} recipients ({failed} failed, {successful_rows} sent).",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to approve campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Approval failed: {str(e)}")


@router.post("/{campaign_id}/reject", response_model=CampaignActionResponse)
async def reject_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Reject campaign."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign.status = CampaignStatus.CANCELLED
        await session.commit()
        
        return CampaignActionResponse(
            success=True,
            message="Campaign rejected",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except Exception as e:
        logger.error(f"Failed to reject campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Rejection failed: {str(e)}")


@router.post("/{campaign_id}/run", response_model=CampaignActionResponse)
async def run_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Run or resume campaign processing."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        if campaign.status not in [CampaignStatus.CREATED, CampaignStatus.PAUSED, CampaignStatus.RUNNING]:
            raise HTTPException(status_code=400, detail=f"Campaign cannot be run in status: {campaign.status.value}")
        
        # Ensure Gmail account is connected
        if not campaign.gmail_account_id:
            # Get default account
            result = await session.execute(
                select(GmailAccount).where(GmailAccount.status == "active")
            )
            account = result.scalar_one_or_none()
            if account:
                campaign.gmail_account_id = account.id
            else:
                raise HTTPException(status_code=400, detail="No Gmail account connected. Please connect your Gmail first.")
            await session.commit()
        
        # Set status to RUNNING
        campaign.status = CampaignStatus.RUNNING
        await session.commit()
        
        # Process pending rows
        result = await session.execute(
            select(CampaignRow).where(
                CampaignRow.campaign_id == campaign_id,
                CampaignRow.status.in_([RowStatus.QUEUED, RowStatus.NORMALIZED])
            )
        )
        pending_rows = result.scalars().all()
        
        logger.info(f"Found {len(pending_rows)} pending rows to process for campaign {campaign_id}")
        
        # Process each row
        # Commit after each row for real-time status updates in the UI
        processed = 0
        failed = 0
        for row in pending_rows:
            try:
                await _process_recipient_row(session, campaign, row)
                processed += 1
                # Commit per row so UI can see real-time status
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to process row {row.id}: {e}")
                row.status = RowStatus.FAILED
                row.error_message = str(e)
                failed += 1
                await session.commit()

        # Final refresh of campaign state
        
        # Check if all done
        total_result = await session.execute(
            select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        )
        total_rows = len(total_result.scalars().all())
        
        # Count successful (not failed) rows
        successful = processed - failed
        
        # Only mark COMPLETED when all rows are successfully processed (no failures)
        # If there are failures, keep campaign as RUNNING so user can retry
        if successful >= total_rows:
            campaign.status = CampaignStatus.COMPLETED
            await session.commit()
        elif failed > 0:
            # Keep as RUNNING if there are failures (allows retry)
            campaign.status = CampaignStatus.RUNNING
            await session.commit()
        
        return CampaignActionResponse(
            success=True,
            message=f"Campaign running. Processed {processed} rows ({failed} failed, {successful} successful).",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Run failed: {str(e)}")


@router.post("/{campaign_id}/toggle-dry-run", response_model=CampaignActionResponse)
@with_retry(max_attempts=5, delay=0.5)
async def toggle_dry_run(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Toggle dry-run mode for a campaign."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Toggle the dry_run flag
        campaign.dry_run = not campaign.dry_run
        await session.commit()
        
        mode = "DRY RUN" if campaign.dry_run else "LIVE"
        logger.info(f"[TOGGLE] Campaign {campaign_id} switched to {mode} mode")
        
        return CampaignActionResponse(
            success=True,
            message=f"Campaign switched to {mode} mode",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TOGGLE] Failed to toggle dry-run: {e}")
        raise HTTPException(status_code=500, detail=f"Toggle failed: {str(e)}")


async def _process_recipient_row(session: AsyncSession, campaign: Campaign, row: CampaignRow):
    """Process a single recipient row."""
    graph = create_recipient_graph(session)
    thread_id = get_recipient_thread_id(campaign.id, row.id)
    
    initial_state = RecipientGraphState(
        campaign_id=campaign.id,
        recipient_id=row.id,
        row_number=row.row_number,
        raw_row=row.raw_row_json,
        dry_run=campaign.dry_run,
    )
    
    result = await graph.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": thread_id}},
    )
    
    # Update row status
    row.status = RowStatus(result.get("status", "failed"))
    await session.flush()


async def _send_recipient_email(session: AsyncSession, campaign: Campaign, row: CampaignRow):
    """Send email for a recipient using existing draft."""
    from app.db.models import EmailDraft, GmailAccount, SendEvent, SendStatus
    from app.services.gmail_client import GmailClient
    from app.services.idempotency_service import IdempotencyService
    from app.core.security import parse_gmail_credentials, validate_gmail_token, mask_sensitive_data
    
    logger.info(f"[API_SEND] Starting send for row {row.id}, email: {mask_sensitive_data(row.recipient_email or '', 3)}")
    
    # Get the draft
    result = await session.execute(
        select(EmailDraft).where(EmailDraft.campaign_row_id == row.id)
    )
    draft = result.scalar_one_or_none()
    
    if not draft:
        logger.error(f"[API_SEND] No email draft found for row {row.id}")
        row.status = RowStatus.FAILED
        row.error_message = "No email draft found"
        await session.flush()
        return
    
    logger.info(f"[API_SEND] Found draft: {draft.id}, subject: {draft.subject[:50]}...")
    
    # Get Gmail account
    if not campaign.gmail_account_id:
        logger.error(f"[API_SEND] No Gmail account connected for campaign {campaign.id}")
        row.status = RowStatus.FAILED
        row.error_message = "No Gmail account connected"
        await session.flush()
        return
    
    gmail_account = await session.get(GmailAccount, campaign.gmail_account_id)
    if not gmail_account:
        logger.error(f"[API_SEND] Gmail account not found: {campaign.gmail_account_id}")
        row.status = RowStatus.FAILED
        row.error_message = "Gmail account not found"
        await session.flush()
        return
    
    logger.info(f"[API_SEND] Using Gmail account: {gmail_account.email}, has_token: {bool(gmail_account.token_encrypted)}")
    
    # Check idempotency
    idempotency_service = IdempotencyService()
    
    existing = await idempotency_service.check_duplicate(
        session, campaign.id,
        row.recipient_email or "",
        draft.subject, draft.plain_text_body
    )
    
    if existing and existing.status == SendStatus.SENT:
        logger.info(f"[API_SEND] Duplicate found, already sent: {existing.id}")
        row.status = RowStatus.SENT
        await session.flush()
        return
    
    # Check dry-run
    if campaign.dry_run:
        logger.info(f"[API_SEND] Dry-run mode, recording preview only")
        await idempotency_service.record_send_attempt(
            session=session,
            campaign_row_id=row.id,
            campaign_id=campaign.id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
            status=SendStatus.DRY_RUN,
            provider_response={"dry_run": True},
        )
        row.status = RowStatus.SENT
        await session.flush()
        return
    
    # Actually send
    error_msg = None
    try:
        logger.info(f"[API_SEND] Parsing credentials for {gmail_account.email}")
        
        if not gmail_account.token_encrypted:
            raise ValueError("Gmail token not found. Please reconnect your Gmail account.")
        
        # Parse credentials using the new helper (handles both JSON and legacy formats)
        try:
            credentials_dict = parse_gmail_credentials(
                gmail_account.token_encrypted,
                gmail_account.refresh_token_encrypted
            )
            if not validate_gmail_token(credentials_dict):
                raise ValueError("Invalid credentials structure")
        except ValueError as e:
            logger.error(f"[API_SEND] Token parsing failed: {e}")
            # Auto-disconnect the corrupted account
            gmail_account.status = "disconnected"
            await session.flush()
            raise ValueError("Gmail authentication token is corrupted. Please go to Settings and reconnect your Gmail account.") from e
        except Exception as e:
            logger.error(f"[API_SEND] Token decryption failed: {type(e).__name__}: {str(e)}")
            # Auto-disconnect the corrupted account
            gmail_account.status = "disconnected"
            await session.flush()
            raise ValueError("Failed to decrypt Gmail token. Please go to Settings and reconnect your Gmail account.") from e
        
        logger.info(f"[API_SEND] Credentials parsed successfully, creating Gmail client...")
        
        client = GmailClient(credentials_dict)
        
        logger.info(f"[API_SEND] Sending email via Gmail API...")
        logger.info(f"[API_SEND]   From: {gmail_account.email}")
        logger.info(f"[API_SEND]   To: {mask_sensitive_data(row.recipient_email or '', 3)}")
        
        result = client.send_email(
            sender=gmail_account.email,
            to=row.recipient_email or "",
            subject=draft.subject,
            plain_text=draft.plain_text_body,
            html_body=draft.html_body,
        )
        
        logger.info(f"[API_SEND] Email sent successfully! Message ID: {result.get('message_id')}")
        
        # Record success and update row status
        await idempotency_service.record_send_attempt(
            session=session,
            campaign_row_id=row.id,
            campaign_id=campaign.id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
            status=SendStatus.SENT,
            provider_response=result,
        )
        
        row.status = RowStatus.SENT
        await session.flush()
        
        logger.info(f"[API_SEND] Email sent to {mask_sensitive_data(row.recipient_email or '', 3)}")
        
    except Exception as e:
        # Handle specific token errors
        if isinstance(e, ValueError) and "Gmail" in str(e):
            logger.error(f"[API_SEND] {str(e)}")
            error_msg = str(e)
        else:
            logger.error(f"[API_SEND] Failed to send email: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"[API_SEND] Stack trace: {traceback.format_exc()}")
            error_msg = f"Failed to send: {str(e)}"
        
        # Record failure in idempotency service
        await idempotency_service.record_send_attempt(
            session=session,
            campaign_row_id=row.id,
            campaign_id=campaign.id,
            recipient_email=row.recipient_email or "",
            subject=draft.subject,
            body=draft.plain_text_body,
            status=SendStatus.FAILED,
            error_message=error_msg,
        )
        
        # Update row status - but don't flush here, let caller handle transaction
        row.status = RowStatus.FAILED
        row.error_message = error_msg
        # Note: We don't flush here because the caller (retry endpoint) manages the transaction


@router.post("/{campaign_id}/pause", response_model=CampaignActionResponse)
async def pause_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Pause campaign processing."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        if campaign.status != CampaignStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Campaign is not running")
        
        campaign.status = CampaignStatus.PAUSED
        await session.commit()
        
        return CampaignActionResponse(
            success=True,
            message="Campaign paused",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except Exception as e:
        logger.error(f"Failed to pause campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Pause failed: {str(e)}")


@router.post("/{campaign_id}/resume", response_model=CampaignActionResponse)
async def resume_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Resume paused campaign."""
    return await run_campaign(campaign_id, session)


@router.post("/{campaign_id}/cancel", response_model=CampaignActionResponse)
async def cancel_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Cancel campaign."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign.status = CampaignStatus.CANCELLED
        await session.commit()
        
        return CampaignActionResponse(
            success=True,
            message="Campaign cancelled",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except Exception as e:
        logger.error(f"Failed to cancel campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Cancel failed: {str(e)}")


@router.delete("/{campaign_id}", response_model=CampaignActionResponse)
async def delete_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete campaign and all associated data."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Delete all campaign rows first (cascade should handle this, but be explicit)
        await session.execute(
            CampaignRow.__table__.delete().where(CampaignRow.campaign_id == campaign_id)
        )
        
        # Delete the campaign
        await session.delete(campaign)
        await session.commit()
        
        logger.info(f"Deleted campaign {campaign_id}")
        
        return CampaignActionResponse(
            success=True,
            message="Campaign deleted successfully",
            campaign_id=campaign_id,
            new_status="deleted",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get campaign details."""
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    return _campaign_to_response(campaign)


@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List campaigns."""
    try:
        result = await session.execute(
            select(Campaign).order_by(Campaign.created_at.desc())
        )
        campaigns = result.scalars().all()
        
        total = len(campaigns)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = campaigns[start:end]
        
        return CampaignListResponse(
            campaigns=[_campaign_to_response(c) for c in paginated],
            total=total,
        )
        
    except Exception as e:
        logger.error(f"Failed to list campaigns: {e}")
        raise HTTPException(status_code=500, detail=f"List failed: {str(e)}")


@router.get("/{campaign_id}/rows", response_model=RecipientListResponse)
async def get_campaign_rows(
    campaign_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: str = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Get campaign rows (recipients)."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        query = select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        
        if status:
            query = query.where(CampaignRow.status == status)
        
        query = query.order_by(CampaignRow.row_number).offset((page - 1) * page_size).limit(page_size)
        
        result = await session.execute(query)
        rows = result.scalars().all()
        
        # Get total count
        count_result = await session.execute(
            select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        )
        total = len(count_result.scalars().all())
        
        return RecipientListResponse(
            campaign_id=campaign_id,
            rows=[
                RecipientRowResponse(
                    id=r.id,
                    campaign_id=r.campaign_id,
                    row_number=r.row_number,
                    recipient_email=r.recipient_email,
                    status=r.status.value,
                    raw_row_json=r.raw_row_json or {},
                    normalized_row_json=r.normalized_row_json or {},
                    eligibility_json=r.eligibility_json or {},
                    personalization_context_json=r.personalization_context_json or {},
                    validation_report_json=r.validation_report_json or {},
                    error_message=r.error_message,
                    errors=r.errors or [],
                    retries=r.retries,
                    created_at=r.created_at.isoformat() if r.created_at else "",
                    updated_at=r.updated_at.isoformat() if r.updated_at else "",
                )
                for r in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get campaign rows: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get rows: {str(e)}")


@router.get("/{campaign_id}/rows/{row_id}/draft", response_model=EmailDraftResponse)
async def get_recipient_draft(
    campaign_id: str,
    row_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get email draft for a specific recipient."""
    try:
        # Verify campaign exists
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Get the row
        row = await session.get(CampaignRow, row_id)
        if not row or row.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Recipient not found")
        
        # Get the draft
        from sqlalchemy import select
        from app.db.models import EmailDraft
        
        result = await session.execute(
            select(EmailDraft).where(EmailDraft.campaign_row_id == row_id)
        )
        draft = result.scalar_one_or_none()
        
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found for this recipient")
        
        return EmailDraftResponse(
            id=draft.id,
            campaign_row_id=draft.campaign_row_id,
            to=row.recipient_email,
            subject=draft.subject,
            plain_text_body=draft.plain_text_body,
            html_body=draft.html_body,
            personalization_fields_used=draft.personalization_fields_used or [],
            key_claims_used=draft.key_claims_used or [],
            generation_confidence=draft.generation_confidence,
            needs_human_review=draft.needs_human_review,
            review_reasons=draft.review_reasons or [],
            created_at=draft.created_at.isoformat() if draft.created_at else None,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get draft: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get draft: {str(e)}")


@router.put("/{campaign_id}/rows/{row_id}/draft", response_model=EmailDraftResponse)
async def update_recipient_draft(
    campaign_id: str,
    row_id: str,
    request: EmailDraftUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update email draft for a specific recipient."""
    try:
        # Verify campaign exists
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Get the row
        row = await session.get(CampaignRow, row_id)
        if not row or row.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Recipient not found")
        
        # Get the draft
        from sqlalchemy import select
        from app.db.models import EmailDraft
        
        result = await session.execute(
            select(EmailDraft).where(EmailDraft.campaign_row_id == row_id)
        )
        draft = result.scalar_one_or_none()
        
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found for this recipient")
        
        # Update fields if provided
        if request.subject is not None:
            draft.subject = request.subject
        if request.plain_text_body is not None:
            draft.plain_text_body = request.plain_text_body
        if request.html_body is not None:
            draft.html_body = request.html_body
        
        # Mark as needing human review since it was manually edited
        draft.needs_human_review = True
        draft.review_reasons = draft.review_reasons or []
        if "Manually edited by user" not in draft.review_reasons:
            draft.review_reasons.append("Manually edited by user")
        
        await session.commit()
        await session.refresh(draft)
        
        return EmailDraftResponse(
            id=draft.id,
            campaign_row_id=draft.campaign_row_id,
            to=row.recipient_email,
            subject=draft.subject,
            plain_text_body=draft.plain_text_body,
            html_body=draft.html_body,
            personalization_fields_used=draft.personalization_fields_used or [],
            key_claims_used=draft.key_claims_used or [],
            generation_confidence=draft.generation_confidence,
            needs_human_review=draft.needs_human_review,
            review_reasons=draft.review_reasons or [],
            created_at=draft.created_at.isoformat() if draft.created_at else None,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update draft: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update draft: {str(e)}")


@router.get("/{campaign_id}/export", response_model=CampaignExportResponse)
async def export_campaign(
    campaign_id: str,
    format: str = Query("csv", pattern="^(csv|json)$"),
    session: AsyncSession = Depends(get_session),
):
    """Export campaign results."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Get all rows
        result = await session.execute(
            select(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
        )
        rows = result.scalars().all()
        
        # Create export file
        export_id = str(uuid.uuid4())
        export_path = os.path.join(settings.UPLOAD_DIR, f"export_{export_id}.{format}")
        
        if format == "csv":
            # Build CSV data
            data = []
            for row in rows:
                data.append({
                    "row_number": row.row_number,
                    "recipient_email": row.recipient_email,
                    "status": row.status.value,
                    **(row.raw_row_json or {}),
                })
            
            df = pd.DataFrame(data)
            df.to_csv(export_path, index=False)
        else:
            # JSON format
            import json
            data = []
            for row in rows:
                data.append({
                    "row_number": row.row_number,
                    "recipient_email": row.recipient_email,
                    "status": row.status.value,
                    "raw_data": row.raw_row_json,
                    "errors": row.errors,
                })
            
            with open(export_path, "w") as f:
                json.dump(data, f, indent=2)
        
        return CampaignExportResponse(
            campaign_id=campaign_id,
            download_url=f"/campaigns/{campaign_id}/export/download?file={export_id}.{format}",
            format=format,
            row_count=len(rows),
        )
        
    except Exception as e:
        logger.error(f"Failed to export campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@router.get("/{campaign_id}/progress", response_model=CampaignProgressResponse)
async def get_campaign_progress(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get campaign progress."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Get counts
        from sqlalchemy import func
        
        result = await session.execute(
            select(
                CampaignRow.status,
                func.count(CampaignRow.id).label("count")
            )
            .where(CampaignRow.campaign_id == campaign_id)
            .group_by(CampaignRow.status)
        )
        status_counts = {row.status.value: row.count for row in result}
        
        total = sum(status_counts.values())
        sent = status_counts.get("sent", 0)
        failed = status_counts.get("failed", 0)
        skipped = status_counts.get("skipped", 0) + status_counts.get("ineligible", 0)
        processed = total - status_counts.get("queued", 0)
        
        percentage = (processed / total * 100) if total > 0 else 0
        
        return CampaignProgressResponse(
            campaign_id=campaign_id,
            status=campaign.status.value,
            total_rows=total,
            processed_rows=processed,
            sent_count=sent,
            failed_count=failed,
            skipped_count=skipped,
            remaining_count=total - processed,
            percentage_complete=round(percentage, 2),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get progress: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get progress: {str(e)}")


@router.put("/{campaign_id}/plan")
async def update_campaign_plan(
    campaign_id: str,
    request: CampaignPlanUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update campaign plan fields (goal, tone, cta, context)."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Check if campaign can be edited
        editable_statuses = [
            CampaignStatus.AWAITING_SCHEMA_REVIEW,
            CampaignStatus.AWAITING_CAMPAIGN_APPROVAL,
            CampaignStatus.READY_TO_SEND,
            CampaignStatus.PAUSED,
        ]
        
        if campaign.status not in editable_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot edit plan when campaign status is '{campaign.status.value}'. Editing is only allowed before sending starts."
            )
        
        # Get current plan
        current_plan = campaign.campaign_plan_json or {}
        
        # Update fields if provided
        if request.inferred_goal is not None:
            current_plan["inferred_goal"] = request.inferred_goal
        if request.tone is not None:
            current_plan["tone"] = request.tone
        if request.cta is not None:
            current_plan["cta"] = request.cta
        if request.context is not None:
            current_plan["context"] = request.context
        
        # Save updated plan
        campaign.campaign_plan_json = current_plan
        await session.commit()
        
        logger.info(f"Updated campaign plan for {campaign_id}")
        
        return CampaignActionResponse(
            success=True,
            message="Campaign plan updated successfully",
            campaign_id=campaign_id,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update campaign plan: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update plan: {str(e)}")


@router.post("/{campaign_id}/regenerate-drafts")
async def regenerate_campaign_drafts(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Regenerate sample drafts using the updated campaign plan."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Check if campaign can be edited
        editable_statuses = [
            CampaignStatus.AWAITING_SCHEMA_REVIEW,
            CampaignStatus.AWAITING_CAMPAIGN_APPROVAL,
            CampaignStatus.READY_TO_SEND,
            CampaignStatus.PAUSED,
        ]
        
        if campaign.status not in editable_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot regenerate drafts when campaign status is '{campaign.status.value}'."
            )
        
        # Check required data exists
        if not campaign.inferred_schema_json:
            raise HTTPException(status_code=400, detail="No schema inferred for this campaign")
        
        if not campaign.csv_path or not os.path.exists(campaign.csv_path):
            raise HTTPException(status_code=400, detail="CSV file not found")
        
        # Import required services
        from app.schemas.csv_inference import CsvSchemaInference, CampaignPlan
        from app.services.draft_generation_service import DraftGenerationService
        from app.services.csv_profiler import CSVProfiler
        from app.services.csv_loader import DataLoader
        
        # Load schema and plan
        schema = CsvSchemaInference(**campaign.inferred_schema_json)
        plan = CampaignPlan(**campaign.campaign_plan_json)
        
        # Get sender name and signature from Gmail account if available
        sender_name = None
        signature = None
        try:
            if campaign.gmail_account_id:
                gmail_account = await session.get(GmailAccount, campaign.gmail_account_id)
                if gmail_account:
                    sender_name = gmail_account.sender_name
                    signature = gmail_account.signature
            
            # If no campaign association, get the active Gmail account
            if not sender_name:
                result = await session.execute(
                    select(GmailAccount).where(GmailAccount.status == "active")
                )
                gmail_account = result.scalar_one_or_none()
                if gmail_account:
                    sender_name = gmail_account.sender_name
                    signature = gmail_account.signature
        except Exception as e:
            logger.warning(f"Could not fetch sender name/signature: {e}")
        
        # Load sample rows
        df = DataLoader.load_file(campaign.csv_path)
        sample_rows = CSVProfiler.get_sample_rows(df, 3)
        
        # Generate new drafts
        draft_service = DraftGenerationService()
        drafts = await draft_service.generate_sample_drafts(
            schema, plan, sample_rows, 3, sender_name, signature
        )
        
        # Update campaign with new drafts
        campaign.sample_drafts_json = [d.model_dump() for d in drafts]
        await session.commit()
        
        logger.info(f"Regenerated {len(drafts)} sample drafts for campaign {campaign_id}")
        
        return CampaignRegenerateDraftsResponse(
            success=True,
            drafts=campaign.sample_drafts_json,
            message=f"Successfully regenerated {len(drafts)} sample drafts",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to regenerate drafts: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to regenerate drafts: {str(e)}")
