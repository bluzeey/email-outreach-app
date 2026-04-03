"""Campaign API endpoints."""

import os
import uuid
from datetime import datetime
from typing import List

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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
    CampaignProgressResponse,
    CampaignResponse,
    CampaignUploadResponse,
)
from app.schemas.recipient import RecipientListResponse, RecipientRowResponse
from app.services.csv_loader import CSVLoader, DataLoader
from app.services.csv_profiler import CSVProfiler

logger = get_logger(__name__)
router = APIRouter()


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
        totals_json=campaign.totals_json or {},
        dispatch_cursor=0,  # Would be tracked separately
        created_at=campaign.created_at.isoformat() if campaign.created_at else "",
        updated_at=campaign.updated_at.isoformat() if campaign.updated_at else "",
        errors=campaign.errors or [],
    )


@router.post("", response_model=CampaignResponse)
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
    """Upload CSV or Excel file for a campaign."""
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
        
        # Update campaign
        campaign.csv_filename = file.filename
        campaign.csv_storage_path = dest_path
        campaign.status = CampaignStatus.PROFILING
        await session.commit()
        
        logger.info(f"Uploaded file for campaign {campaign_id}: {len(df)} rows")
        
        return CampaignUploadResponse(
            campaign_id=campaign_id,
            filename=file.filename,
            row_count=len(df),
            columns=list(df.columns),
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
    session: AsyncSession = Depends(get_session),
):
    """Analyze campaign CSV and generate schema/plan."""
    try:
        campaign = await session.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        if not campaign.csv_storage_path:
            raise HTTPException(status_code=400, detail="No CSV uploaded")
        
        # Create graph and run analysis
        graph = create_campaign_graph(session)
        thread_id = get_campaign_thread_id(campaign_id)
        
        initial_state = CampaignGraphState(
            campaign_id=campaign_id,
            context=campaign.context or "",
            csv_path=campaign.csv_storage_path,
            dry_run=campaign.dry_run,
        )
        
        # Run graph up to plan generation
        result = await graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
        
        # Update campaign status
        campaign.status = CampaignStatus(result.get("status", "awaiting_schema_review"))
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
        logger.error(f"Failed to analyze campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


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
        
        # Update campaign
        campaign.status = CampaignStatus.RUNNING
        await session.commit()
        
        # Resume graph with approval
        graph = create_campaign_graph(session)
        thread_id = get_campaign_thread_id(campaign_id)
        
        # Resume graph
        result = await graph.ainvoke(
            None,  # Continue from checkpoint
            config={"configurable": {"thread_id": thread_id}},
        )
        
        return CampaignActionResponse(
            success=True,
            message="Campaign approved and processing started",
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
                await session.commit()
        
        # Run recipient processing for pending rows
        result = await session.execute(
            select(CampaignRow).where(
                CampaignRow.campaign_id == campaign_id,
                CampaignRow.status.in_([RowStatus.QUEUED, RowStatus.NORMALIZED])
            )
        )
        pending_rows = result.scalars().all()
        
        # Process each row
        processed = 0
        for row in pending_rows:
            try:
                await _process_recipient_row(session, campaign, row)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to process row {row.id}: {e}")
                row.status = RowStatus.FAILED
                row.error_message = str(e)
        
        await session.commit()
        
        return CampaignActionResponse(
            success=True,
            message=f"Campaign running. Processed {processed} rows.",
            campaign_id=campaign_id,
            new_status=campaign.status.value,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Run failed: {str(e)}")


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


@router.get("/{campaign_id}/export", response_model=CampaignExportResponse)
async def export_campaign(
    campaign_id: str,
    format: str = Query("csv", regex="^(csv|json)$"),
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
