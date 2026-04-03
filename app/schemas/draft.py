"""Email draft schemas."""

from pydantic import BaseModel, Field


class GeneratedEmail(BaseModel):
    """Generated email."""
    
    subject: str
    plain_text_body: str
    html_body: str
    personalization_fields_used: list[str] = Field(default_factory=list)
    key_claims_used: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class EmailDraftResponse(BaseModel):
    """Email draft response."""
    
    id: str
    campaign_row_id: str
    subject: str
    plain_text_body: str
    html_body: str
    personalization_fields_used: list[str]
    key_claims_used: list[str]
    needs_human_review: bool
    review_reasons: list[str]
    created_at: str


class SampleDraftsResponse(BaseModel):
    """Sample drafts response."""
    
    campaign_id: str
    drafts: list[dict]
    total_samples: int


class DraftRegenerateRequest(BaseModel):
    """Draft regenerate request."""
    
    instructions: str | None = None
