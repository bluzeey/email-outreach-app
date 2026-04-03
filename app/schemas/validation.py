"""Validation schemas."""

from pydantic import BaseModel, Field


class ValidationReport(BaseModel):
    """Validation report."""
    
    passed: bool
    risk_score: float = 0.0  # 0-100
    issues: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class RowValidationRequest(BaseModel):
    """Row validation request."""
    
    override: bool = False


class RowValidationResponse(BaseModel):
    """Row validation response."""
    
    row_id: str
    passed: bool
    risk_score: float
    issues: list[str]
    requires_review: bool
