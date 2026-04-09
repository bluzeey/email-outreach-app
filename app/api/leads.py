"""Leads API endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import AsyncSessionLocal
from app.db.models import Campaign, CampaignRow, Lead, LeadStatus, LeadTag, RowStatus, SendEvent
from app.schemas.leads import (
    LeadAddTagRequest,
    LeadBulkActionRequest,
    LeadBulkActionResponse,
    LeadFilterParams,
    LeadListResponse,
    LeadRemoveTagRequest,
    LeadResponse,
    LeadStatusUpdateRequest,
    LeadTagCreateRequest,
    LeadTagResponse,
    LeadUpdateRequest,
)
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


def _lead_to_response(lead: Lead, campaign_info: dict = None) -> LeadResponse:
    """Convert lead model to response schema."""
    return LeadResponse(
        id=lead.id,
        email=lead.email,
        first_name=lead.first_name,
        last_name=lead.last_name,
        company=lead.company,
        title=lead.title,
        profile_data_json=lead.profile_data_json or {},
        status=lead.status.value if lead.status else "active",
        has_received_followup=lead.has_received_followup,
        followup_sent_at=lead.followup_sent_at.isoformat() if lead.followup_sent_at else None,
        first_seen_at=lead.first_seen_at.isoformat() if lead.first_seen_at else "",
        last_seen_at=lead.last_seen_at.isoformat() if lead.last_seen_at else "",
        created_at=lead.created_at.isoformat() if lead.created_at else "",
        updated_at=lead.updated_at.isoformat() if lead.updated_at else "",
        tags=[LeadTagResponse(
            id=tag.id,
            name=tag.name,
            description=tag.description,
            color=tag.color,
            created_at=tag.created_at.isoformat() if tag.created_at else "",
        ) for tag in (lead.tags or [])],
        campaign_count=campaign_info.get("campaign_count", 0) if campaign_info else 0,
        last_campaign_id=campaign_info.get("last_campaign_id") if campaign_info else None,
        last_campaign_name=campaign_info.get("last_campaign_name") if campaign_info else None,
    )


@router.get("/tags", response_model=list[LeadTagResponse])
async def list_tags(
    session: AsyncSession = Depends(get_session),
):
    """List all lead tags."""
    result = await session.execute(select(LeadTag).order_by(LeadTag.name))
    tags = result.scalars().all()
    
    return [
        LeadTagResponse(
            id=tag.id,
            name=tag.name,
            description=tag.description,
            color=tag.color,
            created_at=tag.created_at.isoformat() if tag.created_at else "",
        )
        for tag in tags
    ]


@router.post("/tags", response_model=LeadTagResponse)
async def create_tag(
    request: LeadTagCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new lead tag."""
    # Check for duplicate name
    result = await session.execute(
        select(LeadTag).where(LeadTag.name == request.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Tag '{request.name}' already exists")
    
    tag = LeadTag(
        name=request.name,
        description=request.description,
        color=request.color,
    )
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    
    logger.info(f"Created lead tag: {tag.name}")
    
    return LeadTagResponse(
        id=tag.id,
        name=tag.name,
        description=tag.description,
        color=tag.color,
        created_at=tag.created_at.isoformat() if tag.created_at else "",
    )


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a lead tag."""
    tag = await session.get(LeadTag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    await session.delete(tag)
    await session.commit()
    
    logger.info(f"Deleted lead tag: {tag.name}")
    
    return {"success": True, "message": f"Tag '{tag.name}' deleted"}


@router.get("", response_model=LeadListResponse)
async def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: str = Query(None),
    tag_id: str = Query(None),
    campaign_id: str = Query(None),
    search: str = Query(None),
    eligible_for_followup: bool = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """List leads with filtering."""
    query = select(Lead)
    
    # Apply filters
    if status:
        try:
            lead_status = LeadStatus(status)
            query = query.where(Lead.status == lead_status)
        except ValueError:
            pass  # Invalid status, ignore filter
    
    if tag_id:
        query = query.join(Lead.tags).where(LeadTag.id == tag_id)
    
    if search:
        search_term = f"%{search.lower()}%"
        query = query.where(
            or_(
                Lead.email.ilike(search_term),
                Lead.first_name.ilike(search_term),
                Lead.last_name.ilike(search_term),
                Lead.company.ilike(search_term),
            )
        )
    
    # For campaign filter, we need to join with campaign_rows
    if campaign_id:
        query = query.join(CampaignRow).where(CampaignRow.campaign_id == campaign_id)
    
    # For followup eligibility, filter by status and has_received_followup
    if eligible_for_followup is True:
        query = query.where(
            Lead.status == LeadStatus.ACTIVE,
            Lead.has_received_followup == False,
        )
    
    # Get total count
    count_query = select(func.count(Lead.id)).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0
    
    # Apply pagination
    query = query.order_by(Lead.last_seen_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    result = await session.execute(query)
    leads = result.scalars().all()
    
    # Enrich with campaign info
    lead_responses = []
    for lead in leads:
        # Get campaign info for this lead
        campaign_info = {}
        campaign_result = await session.execute(
            select(CampaignRow, Campaign)
            .join(Campaign)
            .where(CampaignRow.lead_id == lead.id)
            .order_by(CampaignRow.created_at.desc())
        )
        campaign_rows = campaign_result.all()
        
        if campaign_rows:
            campaign_info["campaign_count"] = len(campaign_rows)
            campaign_info["last_campaign_id"] = campaign_rows[0].Campaign.id
            campaign_info["last_campaign_name"] = campaign_rows[0].Campaign.name
        
        lead_responses.append(_lead_to_response(lead, campaign_info))
    
    return LeadListResponse(
        leads=lead_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a single lead by ID."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Get campaign info
    campaign_info = {}
    campaign_result = await session.execute(
        select(CampaignRow, Campaign)
        .join(Campaign)
        .where(CampaignRow.lead_id == lead.id)
        .order_by(CampaignRow.created_at.desc())
    )
    campaign_rows = campaign_result.all()
    
    if campaign_rows:
        campaign_info["campaign_count"] = len(campaign_rows)
        campaign_info["last_campaign_id"] = campaign_rows[0].Campaign.id
        campaign_info["last_campaign_name"] = campaign_rows[0].Campaign.name
    
    return _lead_to_response(lead, campaign_info)


@router.put("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: str,
    request: LeadUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update lead information."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Update fields
    if request.first_name is not None:
        lead.first_name = request.first_name
    if request.last_name is not None:
        lead.last_name = request.last_name
    if request.company is not None:
        lead.company = request.company
    if request.title is not None:
        lead.title = request.title
    if request.status is not None:
        try:
            lead.status = LeadStatus(request.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")
    
    await session.commit()
    await session.refresh(lead)
    
    logger.info(f"Updated lead {lead.email}")
    
    return _lead_to_response(lead)


@router.post("/{lead_id}/status", response_model=LeadResponse)
async def update_lead_status(
    lead_id: str,
    request: LeadStatusUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update lead status (convenience endpoint)."""
    lead_service = LeadService(session)
    
    try:
        status = LeadStatus(request.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")
    
    lead = await lead_service.update_lead_status(lead_id, status)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    await session.commit()
    
    return _lead_to_response(lead)


@router.post("/{lead_id}/tags", response_model=LeadResponse)
async def add_tag_to_lead(
    lead_id: str,
    request: LeadAddTagRequest,
    session: AsyncSession = Depends(get_session),
):
    """Add a tag to a lead."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    tag = await session.get(LeadTag, request.tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Check if already tagged
    if tag in lead.tags:
        return _lead_to_response(lead)
    
    lead.tags.append(tag)
    await session.commit()
    await session.refresh(lead)
    
    logger.info(f"Added tag {tag.name} to lead {lead.email}")
    
    return _lead_to_response(lead)


@router.delete("/{lead_id}/tags/{tag_id}", response_model=LeadResponse)
async def remove_tag_from_lead(
    lead_id: str,
    tag_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Remove a tag from a lead."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    tag = await session.get(LeadTag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    if tag in lead.tags:
        lead.tags.remove(tag)
        await session.commit()
        await session.refresh(lead)
        
        logger.info(f"Removed tag {tag.name} from lead {lead.email}")
    
    return _lead_to_response(lead)


@router.post("/{lead_id}/mark-responded", response_model=LeadResponse)
async def mark_lead_responded(
    lead_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Mark lead as responded (blocks all followups globally)."""
    lead_service = LeadService(session)
    
    lead = await lead_service.mark_as_responded(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    await session.commit()
    
    logger.info(f"Marked lead {lead.email} as responded")
    
    return _lead_to_response(lead)


@router.get("/{lead_id}/eligibility")
async def check_lead_eligibility(
    lead_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Check if lead is eligible for followup."""
    lead_service = LeadService(session)
    
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    is_eligible = await lead_service.is_eligible_for_followup(lead_id)
    
    # Build reason
    reasons = []
    if lead.status != LeadStatus.ACTIVE:
        reasons.append(f"Status is {lead.status.value}")
    if lead.has_received_followup:
        reasons.append("Already received followup")
    
    return {
        "lead_id": lead_id,
        "email": lead.email,
        "is_eligible": is_eligible,
        "status": lead.status.value,
        "has_received_followup": lead.has_received_followup,
        "reasons": reasons if not is_eligible else [],
    }


@router.post("/bulk-action", response_model=LeadBulkActionResponse)
async def bulk_action(
    request: LeadBulkActionRequest,
    session: AsyncSession = Depends(get_session),
):
    """Perform bulk action on multiple leads."""
    lead_service = LeadService(session)
    
    processed = 0
    failed = 0
    
    for lead_id in request.lead_ids:
        try:
            if request.action == "update_status":
                if not request.status:
                    failed += 1
                    continue
                try:
                    status = LeadStatus(request.status)
                except ValueError:
                    failed += 1
                    continue
                
                lead = await lead_service.update_lead_status(lead_id, status)
                if lead:
                    processed += 1
                else:
                    failed += 1
                    
            elif request.action == "add_tag":
                if not request.tag_id:
                    failed += 1
                    continue
                
                success = await lead_service.add_tag_to_lead(lead_id, request.tag_id)
                if success:
                    processed += 1
                else:
                    failed += 1
                    
            elif request.action == "remove_tag":
                if not request.tag_id:
                    failed += 1
                    continue
                
                success = await lead_service.remove_tag_from_lead(lead_id, request.tag_id)
                if success:
                    processed += 1
                else:
                    failed += 1
        except Exception as e:
            logger.error(f"Bulk action failed for lead {lead_id}: {e}")
            failed += 1
    
    await session.commit()
    
    action_messages = {
        "update_status": f"Updated status to {request.status}",
        "add_tag": "Added tag",
        "remove_tag": "Removed tag",
    }
    
    return LeadBulkActionResponse(
        success=True,
        message=f"{action_messages.get(request.action, 'Action')} for {processed} leads",
        processed_count=processed,
        failed_count=failed,
    )
