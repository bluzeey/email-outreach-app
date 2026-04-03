"""Unit tests for validation service."""

import pytest

from app.schemas.draft import GeneratedEmail
from app.services.validation_service import ValidationService


class TestValidationService:
    """Tests for ValidationService."""
    
    @pytest.fixture
    def service(self):
        return ValidationService()
    
    def test_validate_valid_draft(self, service):
        """Test validation of a valid draft."""
        draft = GeneratedEmail(
            subject="Test Subject",
            plain_text_body="This is a valid email body.",
            html_body="<p>This is a valid email body.</p>",
            personalization_fields_used=["first_name"],
        )
        
        report = service.validate_draft(draft, "test@example.com", {"first_name": "John"})
        
        assert report.passed
        assert report.risk_score < 30
    
    def test_validate_empty_subject(self, service):
        """Test validation catches empty subject."""
        draft = GeneratedEmail(
            subject="",
            plain_text_body="Body text here.",
            html_body="<p>Body text here.</p>",
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert not report.passed
        assert any("empty" in issue.lower() for issue in report.issues)
    
    def test_validate_empty_body(self, service):
        """Test validation catches empty body."""
        draft = GeneratedEmail(
            subject="Test Subject",
            plain_text_body="",
            html_body="",
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert not report.passed
        assert any("empty" in issue.lower() for issue in report.issues)
    
    def test_validate_placeholder_in_content(self, service):
        """Test validation catches unresolved placeholders."""
        draft = GeneratedEmail(
            subject="Hello {{first_name}}",
            plain_text_body="Hi {{first_name}}, how are you?",
            html_body="<p>Hi {{first_name}}, how are you?</p>",
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert any("placeholder" in issue.lower() for issue in report.issues)
    
    def test_validate_invalid_email(self, service):
        """Test validation catches invalid email."""
        draft = GeneratedEmail(
            subject="Test",
            plain_text_body="Body",
            html_body="<p>Body</p>",
        )
        
        report = service.validate_draft(draft, "not-an-email", {})
        
        assert any("email" in issue.lower() for issue in report.issues)
    
    def test_validate_generic_phrases(self, service):
        """Test validation flags generic phrases."""
        draft = GeneratedEmail(
            subject="Test",
            plain_text_body="I hope this email finds you well. Let's connect.",
            html_body="<p>I hope this email finds you well. Let's connect.</p>",
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert any("generic" in issue.lower() or "hope this email" in issue.lower() for issue in report.issues)
    
    def test_validate_long_subject(self, service):
        """Test validation flags long subject."""
        draft = GeneratedEmail(
            subject="A" * 110,  # Very long subject
            plain_text_body="Body",
            html_body="<p>Body</p>",
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert any("long" in issue.lower() for issue in report.issues)
    
    def test_validate_long_body(self, service):
        """Test validation flags long body."""
        draft = GeneratedEmail(
            subject="Test",
            plain_text_body="Word " * 200,  # 400 words
            html_body="<p>" + ("Word " * 200) + "</p>",
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert any("long" in issue.lower() for issue in report.issues)
    
    def test_validate_no_personalization(self, service):
        """Test validation flags missing personalization."""
        draft = GeneratedEmail(
            subject="Test",
            plain_text_body="Body",
            html_body="<p>Body</p>",
            personalization_fields_used=[],
        )
        
        report = service.validate_draft(draft, "test@example.com", {})
        
        assert any("personalization" in issue.lower() for issue in report.issues)
