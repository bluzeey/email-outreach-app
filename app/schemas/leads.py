"""Lead schemas."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class LeadTagResponse(BaseModel):
    """Lead tag response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    name: str
    description: str | None = None
    color: str | None = None
    created_at: str


class LeadTagCreateRequest(BaseModel):
    """Create lead tag request."""
    
    name: str
    description: str | None = None
    color: str | None = None


class LeadResponse(BaseModel):
    """Lead response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    title: str | None = None
    profile_data_json: dict = Field(default_factory=dict)
    status: str  # active, responded, do_not_contact, bounced
    has_received_followup: bool
    followup_sent_at: str | None = None
    first_seen_at: str
    last_seen_at: str
    created_at: str
    updated_at: str
    
    # Enriched fields
    tags: list[LeadTagResponse] = Field(default_factory=list)
    campaign_count: int = 0
    last_campaign_id: str | None = None
    last_campaign_name: str | None = None


class LeadListResponse(BaseModel):
    """Lead list response."""
    
    leads: list[LeadResponse]
    total: int
    page: int
    page_size: int


class LeadUpdateRequest(BaseModel):
    """Update lead request."""
    
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    title: str | None = None
    status: Literal["active", "responded", "do_not_contact", "bounced"] | None = None


class LeadStatusUpdateRequest(BaseModel):
    """Update lead status request."""
    
    status: Literal["active", "responded", "do_not_contact", "bounced"]


class LeadAddTagRequest(BaseModel):
    """Add tag to lead request."""
    
    tag_id: str


class LeadRemoveTagRequest(BaseModel):
    """Remove tag from lead request."""
    
    tag_id: str


class LeadFilterParams(BaseModel):
    """Lead filter parameters."""
    
    status: str | None = None
    tag_id: str | None = None
    campaign_id: str | None = None
    search: str | None = None  # Search email, name, company
    eligible_for_followup: bool | None = None


class LeadBulkActionRequest(BaseModel):
    """Bulk action on leads request."""
    
    lead_ids: list[str]
    action: Literal["update_status", "add_tag", "remove_tag"]
    status: str | None = None  # For update_status action
    tag_id: str | None = None  # For add_tag/remove_tag actions


class LeadBulkActionResponse(BaseModel):
    """Bulk action response."""
    
    success: bool
    message: str
    processed_count: int
    failed_count: int = 0
