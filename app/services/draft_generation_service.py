"""Draft generation service."""

import json
from typing import Any

from app.core.logging import get_logger
from app.schemas.csv_inference import CampaignPlan, CsvSchemaInference
from app.schemas.draft import GeneratedEmail
from app.services.llm_client import UnifiedLLMClient

logger = get_logger(__name__)


class DraftGenerationService:
    """Service for generating personalized email drafts."""
    
    def __init__(self):
        self.llm_client: UnifiedLLMClient | None = None
        try:
            self.llm_client = UnifiedLLMClient(temperature=0.7)
            if not self.llm_client.is_available():
                logger.warning("No LLM client available for draft generation")
                self.llm_client = None
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
    
    async def generate_draft(
        self,
        schema: CsvSchemaInference,
        campaign_plan: CampaignPlan,
        row_data: dict,
        sender_name: str | None = None,
    ) -> GeneratedEmail:
        """Generate personalized email draft for a single row."""
        
        # Extract recipient email
        recipient_email = None
        if schema.primary_email_column and schema.primary_email_column in row_data:
            recipient_email = row_data[schema.primary_email_column]
        
        # Build personalization context
        personalization_context = self._build_personalization_context(
            schema, row_data
        )
        
        # Generate using LLM if available
        if self.llm_client:
            try:
                draft = await self._generate_with_llm(
                    schema, campaign_plan, row_data, personalization_context, recipient_email, sender_name
                )
                if draft:
                    return draft
            except Exception as e:
                logger.error(f"LLM generation failed, using template: {e}")
        
        # Fallback to template-based generation
        return self._generate_with_template(
            schema, campaign_plan, row_data, personalization_context, recipient_email, sender_name
        )
    
    def _build_personalization_context(
        self,
        schema: CsvSchemaInference,
        row_data: dict,
    ) -> dict:
        """Build personalization context from row data."""
        context = {}
        
        # Add name fields
        name_parts = []
        for col in schema.recipient_name_columns:
            if col in row_data and row_data[col]:
                name_parts.append(row_data[col])
                context[col] = row_data[col]
        
        if name_parts:
            context["first_name"] = name_parts[0]
            context["full_name"] = " ".join(name_parts)
        
        # Add company
        for col in schema.company_columns:
            if col in row_data and row_data[col]:
                context["company"] = row_data[col]
                break
        
        # Add other personalization fields
        for col in schema.personalization_columns:
            if col in row_data and row_data[col]:
                context[col] = row_data[col]
        
        return context
    
    async def _generate_with_llm(
        self,
        schema: CsvSchemaInference,
        campaign_plan: CampaignPlan,
        row_data: dict,
        personalization_context: dict,
        recipient_email: str | None = None,
        sender_name: str | None = None,
    ) -> GeneratedEmail | None:
        """Generate email using LLM."""
        if not self.llm_client:
            return None
        
        # Build prompt
        context_str = "\n".join([f"- {k}: {v}" for k, v in personalization_context.items()])
        
        # Include campaign context if provided
        campaign_context_section = ""
        if campaign_plan.context:
            campaign_context_section = f"""Campaign Context (the product/service you are promoting):
{campaign_plan.context}

"""
        
        # Include sender information
        sender_section = ""
        sign_off_instruction = ""
        if sender_name:
            sender_section = f"""Sender: {sender_name}

"""
            sign_off_instruction = f'- Sign off with "Kind regards," followed by "{sender_name}" on a new line'
        else:
            sign_off_instruction = '- Sign off with "Kind regards"'
        
        prompt = f"""You are an expert at writing personalized cold outreach emails.

{sender_section}{campaign_context_section}Campaign Goal: {campaign_plan.inferred_goal}
Tone: {campaign_plan.tone}
Style Constraints:
{chr(10).join([f"- {c}" for c in campaign_plan.style_constraints])}
Call-to-Action: {campaign_plan.cta}
Subject Style: {campaign_plan.subject_style}

Recipient Information:
{context_str}

Do Not:
{chr(10).join([f"- {c}" for c in campaign_plan.do_not_claim])}

Generate an email with:
1. Subject line (max 60 characters, no quotes)
2. Plain text body (concise, under 150 words)
3. HTML version of the body (basic formatting with <p> tags)

Requirements:
- Reference the product/service from the Campaign Context naturally
- Personalize using the recipient information provided
- Never fabricate facts not in the data
- Use natural, conversational language
- Include the call-to-action naturally
- No generic templates like "I hope this email finds you well"
{sign_off_instruction}

Respond with ONLY valid JSON:
{{
  "subject": "Subject line here",
  "plain_text_body": "Email body here...",
  "html_body": "<p>Email body here...</p>",
  "personalization_fields_used": ["field1", "field2"],
  "key_claims_used": [],
  "confidence": 0.85
}}"""
        
        try:
            from langchain_core.messages import HumanMessage
            response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
            content = response.content
            
            # Extract JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            
            # Validate required fields
            required = ["subject", "plain_text_body", "html_body"]
            for field in required:
                if field not in data or not data[field]:
                    raise ValueError(f"Missing required field: {field}")
            
            # Determine if review needed
            needs_review = data.get("confidence", 0.8) < 0.7
            review_reasons = []
            if needs_review:
                review_reasons.append("Low confidence in generation")
            
            return GeneratedEmail(
                to=recipient_email,
                subject=data["subject"],
                plain_text_body=data["plain_text_body"],
                html_body=data["html_body"],
                personalization_fields_used=data.get("personalization_fields_used", list(personalization_context.keys())),
                key_claims_used=data.get("key_claims_used", []),
                confidence=data.get("confidence", 0.8),
                needs_human_review=needs_review,
                review_reasons=review_reasons,
            )
            
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return None
    
    def _generate_with_template(
        self,
        schema: CsvSchemaInference,
        campaign_plan: CampaignPlan,
        row_data: dict,
        personalization_context: dict,
        recipient_email: str | None = None,
        sender_name: str | None = None,
    ) -> GeneratedEmail:
        """Generate email using templates (fallback)."""
        
        # Extract key info
        first_name = personalization_context.get("first_name", "there")
        company = personalization_context.get("company", "your company")
        
        # Include context if available
        context_intro = ""
        if campaign_plan.context:
            context_intro = f"\n\n{campaign_plan.context}"
        
        # Generate subject
        if company and company != "your company":
            subject = f"Quick question about {company}"
        else:
            subject = "Quick question"
        
        # Build sign-off
        sign_off = "Kind regards"
        if sender_name:
            sign_off = f"Kind regards,\n{sender_name}"
        
        # Generate body
        plain_body = f"""Hi {first_name},

I came across {company} and wanted to reach out.{context_intro}

{campaign_plan.inferred_goal}

{campaign_plan.cta}.

{sign_off}"""
        
        # Simple HTML conversion
        html_body = f"<p>Hi {first_name},</p>\n\n"
        html_body += f"<p>I came across {company} and wanted to reach out.</p>\n\n"
        if context_intro:
            html_body += f"<p>{campaign_plan.context}</p>\n\n"
        html_body += f"<p>{campaign_plan.inferred_goal}</p>\n\n"
        html_body += f"<p>{campaign_plan.cta}.</p>\n\n"
        if sender_name:
            html_body += f"<p>Kind regards,<br>{sender_name}</p>"
        else:
            html_body += "<p>Kind regards</p>"
        
        return GeneratedEmail(
            to=recipient_email,
            subject=subject,
            plain_text_body=plain_body,
            html_body=html_body,
            personalization_fields_used=list(personalization_context.keys()),
            key_claims_used=[],
            confidence=0.6,  # Lower confidence for template
            needs_human_review=True,
            review_reasons=["Generated using fallback template"],
        )
    
    async def generate_sample_drafts(
        self,
        schema: CsvSchemaInference,
        campaign_plan: CampaignPlan,
        sample_rows: list[dict],
        count: int = 3,
        sender_name: str | None = None,
    ) -> list[GeneratedEmail]:
        """Generate sample drafts for review."""
        drafts = []
        
        for row in sample_rows[:count]:
            try:
                draft = await self.generate_draft(schema, campaign_plan, row, sender_name)
                drafts.append(draft)
            except Exception as e:
                logger.error(f"Failed to generate sample draft: {e}")
        
        return drafts
