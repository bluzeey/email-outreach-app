"""LangGraph state models."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class CampaignGraphState(BaseModel):
    """State for campaign graph."""
    
    campaign_id: str
    context: str = ""  # Campaign context/purpose for AI
    gmail_account_id: str | None = None
    csv_path: str | None = None
    csv_profile: dict | None = None
    inferred_schema: dict | None = None
    schema_confidence: float | None = None
    campaign_plan: dict | None = None
    sample_drafts: list[dict] = Field(default_factory=list)
    approval_status: Literal["pending", "approved", "rejected"] = "pending"
    row_ids: list[str] = Field(default_factory=list)
    dispatch_cursor: int = 0
    totals: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    status: Literal[
        "created",
        "profiling",
        "awaiting_schema_review",
        "awaiting_approval_review",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
    ] = "created"
    dry_run: bool = True


class RecipientGraphState(BaseModel):
    """State for recipient graph."""
    
    campaign_id: str
    recipient_id: str
    row_number: int
    raw_row: dict = Field(default_factory=dict)
    normalized_row: dict | None = None
    eligibility: dict | None = None
    personalization_context: dict | None = None
    generated_email: dict | None = None
    validation_report: dict | None = None
    review_required: bool = False
    approval_status: Literal["pending", "approved", "rejected", "not_required"] = "pending"
    send_result: dict | None = None
    retries: int = 0
    status: Literal[
        "queued",
        "normalized",
        "ineligible",
        "generated",
        "validated",
        "awaiting_review",
        "sending",
        "sent",
        "failed",
        "skipped",
        "dry_run_preview",
    ] = "queued"
    errors: list[str] = Field(default_factory=list)
    dry_run: bool = True
