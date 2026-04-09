"""Followup schemas."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FollowupDraftResponse(BaseModel):
    """Followup draft response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    campaign_row_id: str
    subject: str
    plain_text_body: str
    html_body: str
    context_summary: str | None = None
    generation_confidence: int
    needs_human_review: bool
    review_reasons: list[str] = Field(default_factory=list)
    status: str  # draft, approved, rejected, sent
    created_at: str
    updated_at: str
    
    # Enriched fields
    recipient_email: str | None = None
    recipient_name: str | None = None
    original_subject: str | None = None
    campaign_name: str | None = None


class FollowupPreviewRequest(BaseModel):
    """Request to generate a followup draft preview."""
    
    campaign_row_id: str
    tone: Literal["gentle", "polite", "direct"] = "gentle"
    custom_instructions: str | None = None


class FollowupSendRequest(BaseModel):
    """Request to send a followup."""
    
    draft_id: str
    dry_run: bool = True  # Default to dry-run for safety


class FollowupSendResponse(BaseModel):
    """Followup send response."""
    
    success: bool
    message: str
    draft_id: str
    send_event_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    dry_run: bool = True


class FollowupBulkPreviewRequest(BaseModel):
    """Request to generate followup previews for multiple leads."""
    
    lead_ids: list[str]
    tone: Literal["gentle", "polite", "direct"] = "gentle"
    max_count: int = 50  # Limit to prevent abuse


class FollowupBulkSendRequest(BaseModel):
    """Request to send followups in bulk."""
    
    draft_ids: list[str]
    dry_run: bool = True


class FollowupBulkSendResponse(BaseModel):
    """Bulk followup send response."""
    
    success: bool
    message: str
    total_requested: int
    sent_count: int
    failed_count: int
    dry_run: bool
    results: list[dict] = Field(default_factory=list)


class FollowupEligibilityResponse(BaseModel):
    """Lead followup eligibility response."""
    
    lead_id: str
    email: str
    is_eligible: bool
    reasons: list[str] = Field(default_factory=list)
    status: str
    has_received_followup: bool
    campaign_row_id: str | None = None  # The specific campaign row for followup
    last_sent_at: str | None = None


class FollowupStatsResponse(BaseModel):
    """Followup statistics for the account."""
    
    total_leads: int
    eligible_for_followup: int
    already_followed_up: int
    responded: int
    do_not_contact: int
    drafts_pending: int
    drafts_approved: int
