"""Review schemas."""

from pydantic import BaseModel


class RowReviewRequest(BaseModel):
    """Row review request."""
    
    decision: str  # approved, rejected
    notes: str | None = None


class RowReviewResponse(BaseModel):
    """Row review response."""
    
    row_id: str
    decision: str
    new_status: str


class CampaignReviewResponse(BaseModel):
    """Campaign review response."""
    
    campaign_id: str
    decision: str
    new_status: str
