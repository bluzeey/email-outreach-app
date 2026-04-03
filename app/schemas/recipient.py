"""Recipient row schemas."""

from pydantic import BaseModel, ConfigDict, Field


class RecipientRowResponse(BaseModel):
    """Recipient row response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    campaign_id: str
    row_number: int
    recipient_email: str | None = None
    status: str
    raw_row_json: dict = Field(default_factory=dict)
    normalized_row_json: dict = Field(default_factory=dict)
    eligibility_json: dict = Field(default_factory=dict)
    personalization_context_json: dict = Field(default_factory=dict)
    validation_report_json: dict = Field(default_factory=dict)
    error_message: str | None = None
    errors: list[str] = Field(default_factory=list)
    retries: int = 0
    created_at: str
    updated_at: str


class RecipientListResponse(BaseModel):
    """Recipient list response."""
    
    campaign_id: str
    rows: list[RecipientRowResponse]
    total: int
    page: int
    page_size: int


class RecipientDetailResponse(BaseModel):
    """Recipient detail response."""
    
    row: RecipientRowResponse
    email_draft: dict | None = None
    send_event: dict | None = None
