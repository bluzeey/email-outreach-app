"""Followup service for managing followup emails."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    Campaign,
    CampaignRow,
    EmailDraft,
    FollowupDraft,
    Lead,
    LeadStatus,
    RowStatus,
    SendEvent,
    SendStatus,
)
from app.services.draft_generation_service import DraftGenerationService
from app.services.lead_service import LeadService

logger = get_logger(__name__)


class FollowupService:
    """Service for managing followup emails."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.lead_service = LeadService(session)
        self.draft_service = DraftGenerationService()
    
    async def generate_followup_draft(
        self,
        campaign_row_id: str,
        tone: str = "gentle",
        custom_instructions: str | None = None,
    ) -> Optional[FollowupDraft]:
        """Generate a followup draft for a campaign row.
        
        Args:
            campaign_row_id: The campaign row to follow up on
            tone: gentle, polite, or direct
            custom_instructions: Additional instructions for the AI
            
        Returns:
            The generated FollowupDraft or None if generation failed
        """
        # Get campaign row with related data
        row = await self.session.get(CampaignRow, campaign_row_id)
        if not row:
            logger.error(f"Campaign row not found: {campaign_row_id}")
            return None
        
        # Check if already has a followup draft
        result = await self.session.execute(
            select(FollowupDraft)
            .where(FollowupDraft.campaign_row_id == campaign_row_id)
            .where(FollowupDraft.status.in_(["draft", "approved"]))
        )
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"Using existing followup draft for row {campaign_row_id}")
            return existing
        
        # Get original email draft
        result = await self.session.execute(
            select(EmailDraft)
            .where(EmailDraft.campaign_row_id == campaign_row_id)
            .order_by(EmailDraft.created_at.desc())
            .limit(1)
        )
        original_draft = result.scalar_one_or_none()
        
        if not original_draft:
            logger.error(f"No original draft found for row {campaign_row_id}")
            return None
        
        # Get send event for threading info
        send_event = row.send_event
        if not send_event or send_event.status != SendStatus.SENT:
            logger.error(f"No sent email found for row {campaign_row_id}")
            return None
        
        # Get campaign for context
        campaign = await self.session.get(Campaign, row.campaign_id)
        if not campaign:
            logger.error(f"Campaign not found: {row.campaign_id}")
            return None
        
        # Get lead info
        lead = row.lead
        lead_name = ""
        if lead:
            parts = [p for p in [lead.first_name, lead.last_name] if p]
            lead_name = " ".join(parts)
        
        # Generate followup content using AI
        try:
            subject, plain_body, html_body = await self._generate_followup_content(
                original_subject=original_draft.subject,
                original_body=original_draft.plain_text_body,
                recipient_name=lead_name,
                tone=tone,
                custom_instructions=custom_instructions,
                campaign_context=campaign.campaign_plan_json.get("context", ""),
            )
            
            # Create followup draft
            followup = FollowupDraft(
                campaign_row_id=campaign_row_id,
                subject=subject,
                plain_text_body=plain_body,
                html_body=html_body,
                original_send_event_id=send_event.id,
                context_summary=self._summarize_context(original_draft.plain_text_body),
                generation_confidence=85,  # High confidence for followups
                needs_human_review=False,
                review_reasons=[],
                status="draft",
            )
            
            self.session.add(followup)
            await self.session.flush()
            
            logger.info(f"Generated followup draft for row {campaign_row_id}")
            return followup
            
        except Exception as e:
            logger.error(f"Failed to generate followup: {e}")
            return None
    
    async def _generate_followup_content(
        self,
        original_subject: str,
        original_body: str,
        recipient_name: str,
        tone: str,
        custom_instructions: str | None,
        campaign_context: str,
    ) -> tuple[str, str, str]:
        """Generate followup email content using AI."""
        
        from app.services.llm_client import UnifiedLLMClient
        from langchain_core.messages import HumanMessage
        
        llm_client = UnifiedLLMClient(temperature=0.7)
        
        # Build tone instruction
        tone_instructions = {
            "gentle": "Keep it gentle and understanding. Don't assume they saw the first email.",
            "polite": "Be polite but firmer. Reference that you reached out before.",
            "direct": "Be direct and to the point. Mention this is a follow-up.",
        }
        tone_instruction = tone_instructions.get(tone, tone_instructions["gentle"])
        
        # Build custom instructions section
        custom_section = ""
        if custom_instructions:
            custom_section = f"\nAdditional instructions: {custom_instructions}\n"
        
        prompt = f"""You are an expert at writing professional follow-up emails.

Original email sent:
Subject: {original_subject}

Body:
{original_body[:500]}...

Campaign context (what we're promoting):
{campaign_context[:300]}

Recipient name: {recipient_name or "there"}

Follow-up style: {tone_instruction}{custom_section}

Generate a follow-up email with:
1. Subject line (reference the original, e.g., "Re: {original_subject[:40]}" or similar)
2. Plain text body (concise, 2-3 sentences max, friendly but professional)
3. HTML version (basic formatting with <p> tags)

Requirements:
- Keep it brief - follow-ups should be short
- Don't be pushy or apologetic
- Make it easy for them to respond
- No "just checking in" or "circling back" language
- Reference the original email naturally, not mechanically

Respond with ONLY valid JSON:
{{
  "subject": "Subject line here",
  "plain_text_body": "Email body here...",
  "html_body": "<p>Email body here...</p>"
}}"""
        
        try:
            response = await llm_client.ainvoke([HumanMessage(content=prompt)])
            content = response.content
            
            # Extract JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            import json
            data = json.loads(content.strip())
            
            return (
                data["subject"],
                data["plain_text_body"],
                data["html_body"],
            )
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            # Fallback template
            return self._generate_fallback_followup(
                original_subject, recipient_name, tone
            )
    
    def _generate_fallback_followup(
        self,
        original_subject: str,
        recipient_name: str,
        tone: str,
    ) -> tuple[str, str, str]:
        """Generate fallback followup template."""
        name = recipient_name or "there"
        
        subject = f"Re: {original_subject}"
        
        if tone == "gentle":
            plain = f"""Hi {name},

I wanted to follow up on my email below. Would love to hear your thoughts when you have a moment.

Best regards"""
        elif tone == "direct":
            plain = f"""Hi {name},

Following up on my previous email. Are you interested in discussing this further?

Best regards"""
        else:
            plain = f"""Hi {name},

Just following up on the email I sent last week. Let me know if you'd like to chat.

Best regards"""
        
        html = plain.replace("\n\n", "</p><p>").replace("\n", "<br>")
        html = f"<p>{html}</p>"
        
        return subject, plain, html
    
    def _summarize_context(self, original_body: str) -> str:
        """Create a brief summary of the original email context."""
        # Simple truncation-based summary
        lines = original_body.split("\n")
        first_lines = [l for l in lines[:3] if l.strip()]
        return " ".join(first_lines)[:200]
    
    async def approve_followup_draft(self, draft_id: str) -> Optional[FollowupDraft]:
        """Approve a followup draft for sending."""
        draft = await self.session.get(FollowupDraft, draft_id)
        if not draft:
            return None
        
        draft.status = "approved"
        draft.updated_at = datetime.utcnow()
        
        logger.info(f"Approved followup draft {draft_id}")
        return draft
    
    async def reject_followup_draft(self, draft_id: str) -> Optional[FollowupDraft]:
        """Reject a followup draft."""
        draft = await self.session.get(FollowupDraft, draft_id)
        if not draft:
            return None
        
        draft.status = "rejected"
        draft.updated_at = datetime.utcnow()
        
        logger.info(f"Rejected followup draft {draft_id}")
        return draft
    
    async def update_followup_draft(
        self,
        draft_id: str,
        subject: str | None = None,
        plain_text_body: str | None = None,
        html_body: str | None = None,
    ) -> Optional[FollowupDraft]:
        """Update a followup draft."""
        draft = await self.session.get(FollowupDraft, draft_id)
        if not draft:
            return None
        
        if subject is not None:
            draft.subject = subject
        if plain_text_body is not None:
            draft.plain_text_body = plain_text_body
        if html_body is not None:
            draft.html_body = html_body
        
        draft.updated_at = datetime.utcnow()
        
        logger.info(f"Updated followup draft {draft_id}")
        return draft
