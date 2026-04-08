"""Campaign schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CampaignCreateRequest(BaseModel):
    """Create campaign request."""
    
    name: str
    context: str = ""  # Campaign context/purpose (e.g., "inviting researchers to conference")
    dry_run: bool = True


class CampaignResponse(BaseModel):
    """Campaign response schema."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    name: str
    context: str | None = None
    status: str
    dry_run: bool
    csv_filename: str | None = None
    inferred_schema_json: dict = Field(default_factory=dict)
    campaign_plan_json: dict = Field(default_factory=dict)
    sample_drafts_json: list[dict] = Field(default_factory=list)
    totals_json: dict = Field(default_factory=dict)
    dispatch_cursor: int = 0
    created_at: str
    updated_at: str
    errors: list[str] = Field(default_factory=list)


class CampaignListResponse(BaseModel):
    """Campaign list response."""
    
    campaigns: list[CampaignResponse]
    total: int


class CampaignUploadResponse(BaseModel):
    """CSV upload response."""

    campaign_id: str
    filename: str
    row_count: int
    columns: list[str]
    added_rows: int | None = None
    skipped_duplicates: int | None = None
    skipped_invalid: int | None = None
    mode: str | None = None  # "initial" or "append"


class CampaignAnalyzeRequest(BaseModel):
    """Analyze campaign request."""
    
    pass


class CampaignAnalyzeResponse(BaseModel):
    """Analyze campaign response."""
    
    campaign_id: str
    schema_inference: dict
    campaign_plan: dict
    sample_count: int


class CampaignApproveRequest(BaseModel):
    """Approve campaign request."""
    
    notes: str | None = None


class CampaignActionResponse(BaseModel):
    """Campaign action response."""
    
    success: bool
    message: str
    campaign_id: str
    new_status: str | None = None


class CampaignProgressResponse(BaseModel):
    """Campaign progress response."""
    
    campaign_id: str
    status: str
    total_rows: int
    processed_rows: int
    sent_count: int
    failed_count: int
    skipped_count: int
    remaining_count: int
    percentage_complete: float


class CampaignExportResponse(BaseModel):
    """Campaign export response."""
    
    campaign_id: str
    download_url: str
    format: str
    row_count: int


class CampaignPlanUpdateRequest(BaseModel):
    """Update campaign plan request."""
    
    inferred_goal: str | None = None
    tone: str | None = None
    cta: str | None = None
    context: str | None = None


class CampaignRegenerateDraftsResponse(BaseModel):
    """Regenerate drafts response."""
    
    success: bool
    drafts: list[dict]
    message: str
