"""Authentication schemas."""

from pydantic import BaseModel, ConfigDict


class GmailAccountResponse(BaseModel):
    """Gmail account response schema."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    email: str
    sender_name: str | None = None
    signature: str | None = None
    status: str
    connected_at: str
    scopes: list[str]


class UpdateSignatureRequest(BaseModel):
    """Update signature request."""
    
    signature: str | None = None


class AuthUrlResponse(BaseModel):
    """OAuth authorization URL response."""
    
    auth_url: str
    state: str


class AuthCallbackRequest(BaseModel):
    """OAuth callback request."""
    
    code: str
    state: str


class AuthStatusResponse(BaseModel):
    """Authentication status response."""
    
    connected: bool
    account: GmailAccountResponse | None = None
