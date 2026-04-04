"""Security utilities."""

import hashlib
import secrets
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from app.core.config import settings

# Path to store the auto-generated encryption key
KEY_FILE_PATH = Path(".encryption_key")


def get_encryption_key() -> bytes:
    """Get or generate encryption key.
    
    Priority:
    1. ENCRYPTION_KEY from environment/settings
    2. Key from .encryption_key file (auto-generated)
    3. Generate new key and save to file
    """
    # 1. Use provided key from environment if available
    if settings.ENCRYPTION_KEY:
        # Ensure it's properly formatted as base64 (Fernet keys are 32 bytes base64-encoded = 43 chars + padding)
        key = settings.ENCRYPTION_KEY.encode()
        # Validate the key format
        try:
            Fernet(key)
            return key
        except ValueError:
            raise ValueError(
                "Invalid ENCRYPTION_KEY format. Must be a valid Fernet key (32 bytes base64-encoded). "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
    
    # 2. Check for existing key file
    if KEY_FILE_PATH.exists():
        return KEY_FILE_PATH.read_bytes().strip()
    
    # 3. Generate a new key and save it
    key = Fernet.generate_key()
    KEY_FILE_PATH.write_bytes(key)
    # Set restrictive permissions (owner read/write only)
    KEY_FILE_PATH.chmod(0o600)
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
