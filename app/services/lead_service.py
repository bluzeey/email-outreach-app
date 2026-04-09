"""Lead service for managing global leads."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Campaign, CampaignRow, Lead, LeadStatus, LeadTag

logger = get_logger(__name__)


class LeadService:
    """Service for managing global leads."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def upsert_lead_from_row(
        self,
        email: str,
        row_data: dict,
        schema: dict,
        campaign_id: str,
    ) -> Lead:
        """Upsert a lead from campaign row data.
        
        Creates new lead if email doesn't exist, or updates existing lead
        with new information from the row.
        """
        # Normalize email
        email = email.lower().strip()
        
        # Check if lead exists
        result = await self.session.execute(
            select(Lead).where(Lead.email == email)
        )
        lead = result.scalar_one_or_none()
        
        # Extract profile info from row data using schema
        first_name = None
        last_name = None
        company = None
        title = None
        
        # Try to get name from schema-defined columns
        name_cols = schema.get("recipient_name_columns", [])
        if name_cols:
            first_name = row_data.get(name_cols[0], "")
            if len(name_cols) > 1:
                last_name = row_data.get(name_cols[1], "")
        
        # Try first_name/last_name columns directly
        if not first_name:
            first_name = row_data.get("first_name", "") or row_data.get("firstname", "")
        if not last_name:
            last_name = row_data.get("last_name", "") or row_data.get("lastname", "")
        
        # Try company columns
        company_cols = schema.get("company_columns", [])
        if company_cols:
            company = row_data.get(company_cols[0], "")
        if not company:
            company = row_data.get("company", "") or row_data.get("organization", "")
        
        # Try title
        title = row_data.get("title", "") or row_data.get("job_title", "") or row_data.get("role", "")
        
        # Build profile data from remaining fields
        profile_data = {}
        for key, value in row_data.items():
            if key not in ["email", "first_name", "last_name", "company", "title", "firstname", "lastname"]:
                if value and str(value).strip():
                    profile_data[key] = value
        
        if lead:
            # Update existing lead with any new info
            updated = False
            
            if first_name and not lead.first_name:
                lead.first_name = first_name
                updated = True
            if last_name and not lead.last_name:
                lead.last_name = last_name
                updated = True
            if company and not lead.company:
                lead.company = company
                updated = True
            if title and not lead.title:
                lead.title = title
                updated = True
            
            # Merge profile data
            if profile_data:
                current_profile = lead.profile_data_json or {}
                for key, value in profile_data.items():
                    if key not in current_profile:
                        current_profile[key] = value
                lead.profile_data_json = current_profile
                updated = True
            
            # Always update last_seen_at
            lead.last_seen_at = datetime.utcnow()
            
            if updated:
                logger.info(f"Updated lead {lead.email} with new data from campaign {campaign_id}")
            
            return lead
        
        else:
            # Create new lead
            lead = Lead(
                email=email,
                first_name=first_name,
                last_name=last_name,
                company=company,
                title=title,
                profile_data_json=profile_data,
                status=LeadStatus.ACTIVE,
                has_received_followup=False,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
            )
            self.session.add(lead)
            await self.session.flush()  # Get the ID
            
            logger.info(f"Created new lead {email} from campaign {campaign_id}")
            return lead
    
    async def get_or_create_lead(self, email: str) -> Lead:
        """Get existing lead or create minimal new lead."""
        email = email.lower().strip()
        
        result = await self.session.execute(
            select(Lead).where(Lead.email == email)
        )
        lead = result.scalar_one_or_none()
        
        if lead:
            return lead
        
        lead = Lead(
            email=email,
            status=LeadStatus.ACTIVE,
            has_received_followup=False,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        self.session.add(lead)
        await self.session.flush()
        
        logger.info(f"Created minimal lead {email}")
        return lead
    
    async def update_lead_status(
        self,
        lead_id: str,
        status: LeadStatus,
    ) -> Optional[Lead]:
        """Update lead lifecycle status."""
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        
        lead.status = status
        lead.updated_at = datetime.utcnow()
        
        logger.info(f"Updated lead {lead.email} status to {status.value}")
        return lead
    
    async def mark_as_responded(self, lead_id: str) -> Optional[Lead]:
        """Mark lead as responded (blocks followups globally)."""
        return await self.update_lead_status(lead_id, LeadStatus.RESPONDED)
    
    async def mark_followup_sent(self, lead_id: str) -> Optional[Lead]:
        """Mark that lead has received a followup."""
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        
        lead.has_received_followup = True
        lead.followup_sent_at = datetime.utcnow()
        lead.updated_at = datetime.utcnow()
        
        logger.info(f"Marked followup sent for lead {lead.email}")
        return lead
    
    async def is_eligible_for_followup(self, lead_id: str) -> bool:
        """Check if lead is eligible for followup.
        
        Criteria:
        - Status is ACTIVE (not RESPONDED, DO_NOT_CONTACT, or BOUNCED)
        - Has not already received a followup (one max)
        """
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return False
        
        # Check status
        if lead.status != LeadStatus.ACTIVE:
            logger.debug(f"Lead {lead.email} not eligible: status is {lead.status.value}")
            return False
        
        # Check if already received followup
        if lead.has_received_followup:
            logger.debug(f"Lead {lead.email} not eligible: already received followup")
            return False
        
        return True
    
    async def add_tag_to_lead(self, lead_id: str, tag_id: str) -> bool:
        """Add a tag to a lead."""
        lead = await self.session.get(Lead, lead_id)
        tag = await self.session.get(LeadTag, tag_id)
        
        if not lead or not tag:
            return False
        
        # Check if already tagged
        if tag in lead.tags:
            return True
        
        lead.tags.append(tag)
        logger.info(f"Added tag {tag.name} to lead {lead.email}")
        return True
    
    async def remove_tag_from_lead(self, lead_id: str, tag_id: str) -> bool:
        """Remove a tag from a lead."""
        lead = await self.session.get(Lead, lead_id)
        tag = await self.session.get(LeadTag, tag_id)
        
        if not lead or not tag:
            return False
        
        if tag in lead.tags:
            lead.tags.remove(tag)
            logger.info(f"Removed tag {tag.name} from lead {lead.email}")
            return True
        
        return False
