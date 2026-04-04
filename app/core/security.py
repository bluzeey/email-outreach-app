"""Security utilities."""

import hashlib
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
