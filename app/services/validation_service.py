"""Validation service."""

import re
from typing import Any

from email_validator import validate_email, EmailNotValidError

from app.core.logging import get_logger
from app.schemas.draft import GeneratedEmail
from app.schemas.validation import ValidationReport
from app.services.llm_client import UnifiedLLMClient

logger = get_logger(__name__)


class ValidationService:
    """Service for validating generated email drafts."""
    
    # Patterns for detecting issues
    PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]+\}\}|\$\{[^}]+\}|\[placeholder\]", re.IGNORECASE)
    GENERIC_PATTERNS = [
        r"\bI hope this email finds you well\b",
        r"\bTo whom it may concern\b",
        r"\bDear Sir/Madam\b",
        r"\bDear valued customer\b",
        r"\bI wanted to reach out\b",
    ]
    
    def __init__(self):
        self.llm_client: UnifiedLLMClient | None = None
        try:
            self.llm_client = UnifiedLLMClient(temperature=0.1)
            if not self.llm_client.is_available():
                logger.warning("No LLM client available for validation")
                self.llm_client = None
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
    
    def validate_draft(
        self,
        draft: GeneratedEmail,
        recipient_email: str | None,
        row_data: dict,
    ) -> ValidationReport:
        """Validate a generated email draft."""
        
        issues = []
        risk_score = 0.0
        
        # Deterministic checks
        
        # 1. Check for empty fields
        if not draft.subject or not draft.subject.strip():
            issues.append("Subject line is empty")
            risk_score += 20
        
        if not draft.plain_text_body or not draft.plain_text_body.strip():
            issues.append("Email body is empty")
            risk_score += 20
        
        if not draft.html_body or not draft.html_body.strip():
            issues.append("HTML body is empty")
            risk_score += 10
        
        # 2. Check for unresolved placeholders
        for field in [draft.subject, draft.plain_text_body, draft.html_body]:
            if self.PLACEHOLDER_PATTERN.search(field or ""):
                issues.append("Unresolved placeholder detected in content")
                risk_score += 15
                break
        
        # 3. Check for generic phrases
        combined_text = f"{draft.subject} {draft.plain_text_body}"
        for pattern in self.GENERIC_PATTERNS:
            if re.search(pattern, combined_text, re.IGNORECASE):
                issues.append(f"Generic phrase detected: {pattern}")
                risk_score += 5
        
        # 4. Validate recipient email
        if recipient_email:
            try:
                validate_email(recipient_email)
            except EmailNotValidError as e:
                issues.append(f"Invalid recipient email: {e}")
                risk_score += 25
        else:
            issues.append("No recipient email provided")
            risk_score += 25
        
        # 5. Check content length
        word_count = len(draft.plain_text_body.split())
        if word_count > 300:
            issues.append(f"Email body is too long ({word_count} words, max 300)")
            risk_score += 10
        if word_count < 20:
            issues.append(f"Email body is very short ({word_count} words)")
            risk_score += 5
        
        # 6. Check subject length
        if len(draft.subject) > 100:
            issues.append(f"Subject is too long ({len(draft.subject)} chars, max 100)")
            risk_score += 5
        
        # 7. Check for personalization
        if not draft.personalization_fields_used:
            issues.append("No personalization fields were used")
            risk_score += 10
        
        # Determine if validation passed
        passed = risk_score < 30 and len([i for i in issues if "empty" in i or "Invalid" in i or "placeholder" in i]) == 0
        
        # Determine if human review required
        requires_review = risk_score > 40 or draft.needs_human_review
        
        # Suggested fixes
        suggested_fixes = []
        if "Generic phrase detected" in str(issues):
            suggested_fixes.append("Replace generic phrases with more specific, personalized content")
        if "No personalization" in str(issues):
            suggested_fixes.append("Include at least one personalization field in the email")
        if word_count > 300:
            suggested_fixes.append("Shorten the email to under 150 words for better engagement")
        
        return ValidationReport(
            passed=passed,
            risk_score=min(risk_score, 100),
            issues=issues,
            suggested_fixes=suggested_fixes,
            requires_human_review=requires_review,
        )
    
    async def validate_with_llm(
        self,
        draft: GeneratedEmail,
        row_data: dict,
    ) -> ValidationReport | None:
        """Additional LLM-based validation."""
        if not self.llm_client:
            return None
        
        import json
        from langchain.schema import HumanMessage
        
        prompt = f"""You are an expert email copy reviewer. Analyze this email draft for quality and potential issues.

Email Subject: {draft.subject}
Email Body:
{draft.plain_text_body}

Recipient Data: {json.dumps(row_data, indent=2)}

Evaluate on:
1. Hallucination risk (did it invent facts not in the data?)
2. Tone appropriateness
3. Personalization quality
4. CTA clarity
5. Any red flags or concerns

Respond with JSON:
{{
  "risk_score": 0-100,
  "issues": ["issue1", "issue2"],
  "suggested_fixes": ["fix1", "fix2"],
  "requires_human_review": true/false
}}"""
        
        try:
            response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
            content = response.content
            
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            result = json.loads(content.strip())
            
            provider_info = self.llm_client.get_provider_info()
            logger.debug(
                "LLM validation completed",
                provider=provider_info.get("provider"),
                risk_score=result.get("risk_score"),
            )
            
            return ValidationReport(
                passed=result.get("risk_score", 50) < 40,
                risk_score=result.get("risk_score", 50),
                issues=result.get("issues", []),
                suggested_fixes=result.get("suggested_fixes", []),
                requires_human_review=result.get("requires_human_review", False),
            )
            
        except Exception as e:
            logger.error(f"LLM validation error: {e}")
            return None
