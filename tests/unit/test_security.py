"""Unit tests for security utilities."""

import pytest

from app.core.security import (
    generate_csrf_token,
    generate_idempotency_key,
    mask_sensitive_data,
    verify_csrf_token,
)


class TestSecurityUtils:
    """Tests for security utilities."""
    
    def test_generate_idempotency_key_deterministic(self):
        """Test idempotency key is deterministic."""
        key1 = generate_idempotency_key("camp1", "test@example.com", "Subject", "Body")
        key2 = generate_idempotency_key("camp1", "test@example.com", "Subject", "Body")
        
        assert key1 == key2
        assert len(key1) == 64  # SHA256 hex length
    
    def test_generate_idempotency_key_unique(self):
        """Test idempotency keys are unique for different inputs."""
        key1 = generate_idempotency_key("camp1", "test@example.com", "Subject1", "Body")
        key2 = generate_idempotency_key("camp1", "test@example.com", "Subject2", "Body")
        
        assert key1 != key2
    
    def test_generate_csrf_token(self):
        """Test CSRF token generation."""
        token = generate_csrf_token()
        
        assert len(token) > 20
        assert isinstance(token, str)
    
    def test_verify_csrf_token_valid(self):
        """Test CSRF token verification with valid token."""
        token = generate_csrf_token()
        
        assert verify_csrf_token(token, token) is True
    
    def test_verify_csrf_token_invalid(self):
        """Test CSRF token verification with invalid token."""
        token1 = generate_csrf_token()
        token2 = generate_csrf_token()
        
        assert verify_csrf_token(token1, token2) is False
    
    def test_mask_sensitive_data_short(self):
        """Test masking short sensitive data."""
        masked = mask_sensitive_data("abc", visible_chars=4)
        
        assert masked == "***"
    
    def test_mask_sensitive_data_long(self):
        """Test masking long sensitive data."""
        data = "supersecrettoken123"
        masked = mask_sensitive_data(data, visible_chars=4)
        
        assert masked.startswith("supe")
        assert masked.endswith("*************")
        assert len(masked) == len(data)
