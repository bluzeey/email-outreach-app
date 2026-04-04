"""Security utilities."""

import hashlib
import json
import secrets
from typing import Optional

from cryptography.fernet import Fernet

from app.core.config import settings


def get_encryption_key() -> bytes:
    """Get encryption key from ENCRYPTION_KEY env variable.
    
    Raises:
        ValueError: If ENCRYPTION_KEY is not set or is invalid
    """
    if not settings.ENCRYPTION_KEY:
        raise ValueError(
            "ENCRYPTION_KEY is not set. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "Then add it to your .env file as ENCRYPTION_KEY=<key>"
        )
    
    key = settings.ENCRYPTION_KEY.encode()
    
    # Validate the key format
    try:
        Fernet(key)
    except ValueError as e:
        raise ValueError(
            f"Invalid ENCRYPTION_KEY format: {e}. "
            "Must be a valid Fernet key (32 bytes base64-encoded). "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    
    return key


def encrypt_token(token: str) -> str:
    """Encrypt an OAuth token."""
    key = get_encryption_key()
    f = Fernet(key)
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt an OAuth token."""
    key = get_encryption_key()
    f = Fernet(key)
    return f.decrypt(encrypted_token.encode()).decode()


def parse_gmail_credentials(
    encrypted_token: str,
    encrypted_refresh_token: Optional[str] = None
) -> dict:
    """Parse Gmail credentials from encrypted storage.
    
    Supports both formats:
    1. JSON credentials (new format) - decrypted string is a JSON object
    2. Raw token string (legacy format) - decrypted string is just the access token
    
    Args:
        encrypted_token: The encrypted access token or JSON credentials
        encrypted_refresh_token: Optional encrypted refresh token (legacy format)
    
    Returns:
        dict: Credentials dictionary suitable for GmailClient
    
    Raises:
        ValueError: If token is corrupted or cannot be decrypted
    """
    try:
        decrypted = decrypt_token(encrypted_token)
    except Exception as e:
        raise ValueError(f"Failed to decrypt Gmail token: {str(e)}") from e
    
    # Try to parse as JSON first (new format)
    try:
        creds = json.loads(decrypted)
        if isinstance(creds, dict) and "token" in creds:
            # Valid JSON credentials format
            return creds
    except json.JSONDecodeError:
        # Not JSON, treat as raw token (legacy format)
        pass
    
    # Legacy format: raw token string - construct full credentials dict
    token = decrypted
    
    # Try to get refresh token if available (legacy format)
    refresh_token = None
    if encrypted_refresh_token:
        try:
            refresh_token = decrypt_token(encrypted_refresh_token)
        except Exception:
            # Refresh token might be corrupted, but we can still try with access token
            pass
    
    # Build full credentials dict from settings and token
    return {
        "token": token,
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "scopes": ["https://www.googleapis.com/auth/gmail.send", "openid", "https://www.googleapis.com/auth/userinfo.email"],
    }


def validate_gmail_token(credentials_dict: dict) -> bool:
    """Validate that Gmail credentials have required fields.
    
    Args:
        credentials_dict: Credentials dictionary from parse_gmail_credentials
    
    Returns:
        bool: True if valid, False otherwise
    """
    required_fields = ["token", "token_uri", "client_id", "client_secret"]
    for field in required_fields:
        if not credentials_dict.get(field):
            return False
    return True


def generate_idempotency_key(campaign_id: str, recipient_email: str, subject: str, body: str) -> str:
    """Generate idempotency key for a send attempt."""
    content = f"{campaign_id}:{recipient_email}:{subject}:{body}"
    return hashlib.sha256(content.encode()).hexdigest()


def generate_csrf_token() -> str:
    """Generate a CSRF token."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(token: str, expected: str) -> bool:
    """Verify a CSRF token."""
    return secrets.compare_digest(token, expected)


def mask_sensitive_data(data: str, visible_chars: int = 4) -> str:
    """Mask sensitive data for logging."""
    if len(data) <= visible_chars:
        return "*" * len(data)
    return data[:visible_chars] + "*" * (len(data) - visible_chars)
