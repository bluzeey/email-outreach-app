"""CSV inference schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class FilterRule(BaseModel):
    """Filter rule for CSV processing."""
    
    column: str
    operator: Literal["eq", "neq", "in", "not_in", "is_null", "not_null", "truthy", "falsy"]
    value: str | list[str] | None = None
    reason: str


class CsvColumnProfile(BaseModel):
    """CSV column profile."""
    
    name: str
    inferred_type: str  # string, number, boolean, date, email, url
    null_percentage: float
    unique_count: int
    sample_values: list[str]
    is_email: bool = False
    is_url: bool = False
    is_phone: bool = False
    is_date: bool = False
    is_boolean: bool = False


class CsvProfile(BaseModel):
    """CSV profile."""
    
    total_rows: int
    total_columns: int
    columns: list[CsvColumnProfile]
    column_names: list[str]


class CsvSchemaInference(BaseModel):
    """Inferred CSV schema."""
    
    primary_email_column: str
    recipient_name_columns: list[str] = Field(default_factory=list)
    company_columns: list[str] = Field(default_factory=list)
    personalization_columns: list[str] = Field(default_factory=list)
    segmentation_columns: list[str] = Field(default_factory=list)
    blocker_rules: list[FilterRule] = Field(default_factory=list)
    send_rules: list[FilterRule] = Field(default_factory=list)
    inferred_goal: str
    confidence: float
    unresolved_questions: list[str] = Field(default_factory=list)


class CampaignPlan(BaseModel):
    """Campaign plan."""
    
    campaign_name: str
    inferred_goal: str
    target_audience: str
    tone: str
    style_constraints: list[str] = Field(default_factory=list)
    cta: str
    subject_style: str
    personalization_priority: list[str] = Field(default_factory=list)
    do_not_claim: list[str] = Field(default_factory=list)
    review_policy: dict = Field(default_factory=dict)
    sending_policy: dict = Field(default_factory=dict)
    context: str = ""  # User-provided campaign context (e.g., about Granveo)
