"""Schema inference service using LLM."""

import json
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.csv_inference import (
    CampaignPlan,
    CsvColumnProfile,
    CsvProfile,
    CsvSchemaInference,
    FilterRule,
)
from app.services.csv_profiler import CSVProfiler
from app.services.llm_client import UnifiedLLMClient

logger = get_logger(__name__)


class SchemaInferenceService:
    """Service for inferring CSV schema using LLM."""
    
    def __init__(self):
        self.llm_client: UnifiedLLMClient | None = None
        try:
            self.llm_client = UnifiedLLMClient(temperature=0.1)
            if not self.llm_client.is_available():
                logger.warning("No LLM client available for schema inference")
                self.llm_client = None
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
    
    async def infer_schema(
        self,
        profile: CsvProfile,
        sample_rows: list[dict],
    ) -> CsvSchemaInference:
        """Infer CSV schema using deterministic + LLM hybrid approach."""
        
        # Step 1: Deterministic detection
        email_col = CSVProfiler.detect_email_column(profile)
        name_cols = CSVProfiler.detect_name_columns(profile)
        company_cols = CSVProfiler.detect_company_columns(profile)
        blocker_cols = CSVProfiler.detect_blocker_columns(profile)
        
        # Step 2: Build personalization columns (non-email, useful for content)
        personalization_cols = []
        for col in profile.columns:
            if col.name == email_col:
                continue
            if col.name in name_cols or col.name in company_cols:
                personalization_cols.append(col.name)
            # Add other potentially useful columns
            useful_types = ["string", "url"]
            if col.inferred_type in useful_types and col.null_percentage < 50:
                if col.name not in personalization_cols:
                    personalization_cols.append(col.name)
        
        # Step 3: Build segmentation columns
        segmentation_cols = []
        for col in profile.columns:
            if col.inferred_type in ["boolean"] or col.unique_count < 20:
                if col.name not in [email_col] + personalization_cols:
                    segmentation_cols.append(col.name)
        
        # Step 4: Build blocker rules
        blocker_rules = []
        for col_name in blocker_cols:
            # Check if it looks like a boolean/opt-out column
            col_profile = next((c for c in profile.columns if c.name == col_name), None)
            if col_profile:
                if col_profile.is_boolean:
                    blocker_rules.append(FilterRule(
                        column=col_name,
                        operator="truthy",
                        value=None,
                        reason=f"Row blocked by {col_name}",
                    ))
                else:
                    blocker_rules.append(FilterRule(
                        column=col_name,
                        operator="eq",
                        value="yes",
                        reason=f"Row opted out via {col_name}",
                    ))
        
        # Step 5: Infer campaign goal (basic heuristic, can be enhanced with LLM)
        inferred_goal = self._infer_goal_from_columns(profile, personalization_cols)
        
        # Step 6: Calculate confidence
        confidence = self._calculate_confidence(
            email_col,
            personalization_cols,
            sample_rows,
        )
        
        # Step 7: Check for unresolved questions
        unresolved = []
        if not email_col:
            unresolved.append("Could not detect primary email column")
        if not name_cols:
            unresolved.append("Could not detect recipient name columns")
        if confidence < 0.7:
            unresolved.append("Low confidence in schema inference - please review")
        
        # If LLM available, enhance with LLM inference
        if self.llm_client and confidence < 0.9:
            try:
                llm_inference = await self._llm_schema_enhancement(
                    profile, sample_rows, email_col, personalization_cols
                )
                if llm_inference:
                    # Merge LLM insights
                    if llm_inference.get("inferred_goal"):
                        inferred_goal = llm_inference["inferred_goal"]
                    if llm_inference.get("additional_personalization"):
                        for col in llm_inference["additional_personalization"]:
                            if col not in personalization_cols:
                                personalization_cols.append(col)
            except Exception as e:
                logger.warning(f"LLM enhancement failed, using deterministic: {e}")
        
        return CsvSchemaInference(
            primary_email_column=email_col or "",
            recipient_name_columns=name_cols,
            company_columns=company_cols,
            personalization_columns=personalization_cols,
            segmentation_columns=segmentation_cols,
            blocker_rules=blocker_rules,
            send_rules=[],  # Can be populated based on business logic
            inferred_goal=inferred_goal,
            confidence=confidence,
            unresolved_questions=unresolved,
        )
    
    def _infer_goal_from_columns(
        self,
        profile: CsvProfile,
        personalization_cols: list[str],
    ) -> str:
        """Infer campaign goal from column names."""
        all_columns = " ".join([c.name.lower() for c in profile.columns])
        
        # Keyword-based inference
        if any(kw in all_columns for kw in ["sales", "prospect", "lead", "deal"]):
            return "Sales outreach - generate interest and schedule meetings"
        elif any(kw in all_columns for kw in ["partner", "partnership", "collaborate"]):
            return "Partnership outreach - explore collaboration opportunities"
        elif any(kw in all_columns for kw in ["job", "hire", "recruit", "talent"]):
            return "Recruitment outreach - attract candidates"
        elif any(kw in all_columns for kw in ["investor", "funding", "vc", "angel"]):
            return "Investor outreach - pitch for funding"
        elif any(kw in all_columns for kw in ["event", "conference", "webinar", "meetup"]):
            return "Event outreach - drive attendance or participation"
        elif any(kw in all_columns for kw in ["press", "media", "journalist", "pr"]):
            return "PR outreach - media coverage and press mentions"
        else:
            return "General business outreach - establish connections and explore opportunities"
    
    def _calculate_confidence(
        self,
        email_col: str | None,
        personalization_cols: list[str],
        sample_rows: list[dict],
    ) -> float:
        """Calculate confidence score for schema inference."""
        confidence = 0.5
        
        if email_col:
            confidence += 0.3
        if personalization_cols:
            confidence += 0.1 * min(len(personalization_cols), 3)
        if len(sample_rows) >= 3:
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    async def _llm_schema_enhancement(
        self,
        profile: CsvProfile,
        sample_rows: list[dict],
        detected_email: str | None,
        detected_personalization: list[str],
    ) -> dict | None:
        """Use LLM to enhance schema inference."""
        if not self.llm_client:
            return None
        
        # Build prompt
        columns_info = "\n".join([
            f"- {c.name}: type={c.inferred_type}, null={c.null_percentage:.1f}%, unique={c.unique_count}"
            for c in profile.columns
        ])
        
        samples_info = "\n".join([
            f"Row {i+1}: {json.dumps(row, indent=2)}"
            for i, row in enumerate(sample_rows[:3])
        ])
        
        prompt = f"""You are an expert at analyzing CSV data for email outreach campaigns.

Given the following CSV structure and sample rows, analyze what the campaign is likely about and what columns would be most valuable for personalization.

Columns:
{columns_info}

Sample Rows:
{samples_info}

Already detected:
- Email column: {detected_email or "NOT DETECTED"}
- Personalization columns: {detected_personalization}

Provide your analysis as JSON with these fields:
- inferred_goal: A clear statement of what this outreach campaign is trying to achieve
- additional_personalization: List of column names that could be useful for personalization but weren't detected
- tone_suggestion: Suggested tone for emails (professional, casual, formal, friendly, etc.)
- key_insight: One key insight about the data

Respond with only valid JSON."""
        
        try:
            from langchain_core.messages import HumanMessage
            response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
            content = response.content
            
            # Extract JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            result = json.loads(content.strip())
            provider_info = self.llm_client.get_provider_info()
            logger.info(
                "LLM schema enhancement completed",
                provider=provider_info.get("provider"),
                primary_used=provider_info.get("primary_available"),
            )
            return result
            
        except Exception as e:
            logger.error(f"LLM enhancement error: {e}")
            return None
    
    async def generate_campaign_plan(
        self,
        schema: CsvSchemaInference,
        sample_rows: list[dict],
    ) -> CampaignPlan:
        """Generate campaign plan based on inferred schema."""
        
        # Default plan based on schema
        plan = CampaignPlan(
            campaign_name="Email Outreach Campaign",
            inferred_goal=schema.inferred_goal,
            target_audience="Prospects from uploaded list",
            tone="professional",
            style_constraints=[
                "Keep emails concise (under 150 words)",
                "Use natural, conversational language",
                "Avoid overly salesy language",
            ],
            cta="Reply to this email",
            subject_style="short and personalized",
            personalization_priority=schema.personalization_columns[:5],
            do_not_claim=[
                "Don't fabricate personal facts about recipients",
                "Don't claim partnerships or achievements without verification",
                "Don't use misleading subject lines",
            ],
            review_policy={
                "campaign_approval_required": True,
                "row_level_review_threshold": 0.7,
                "high_risk_triggers": ["missing_required_fields", "unusual_personalization"],
            },
            sending_policy={
                "rate_limit_per_minute": settings.MAX_SEND_RATE_PER_MINUTE,
                "max_concurrent_sends": settings.MAX_CONCURRENT_SENDS,
                "retry_failed": True,
                "max_retries": 3,
            },
        )
        
        # Enhance with LLM if available
        if self.llm_client:
            try:
                enhanced = await self._llm_campaign_plan_enhancement(schema, sample_rows, plan)
                if enhanced:
                    # Update plan with LLM suggestions
                    if enhanced.get("tone"):
                        plan.tone = enhanced["tone"]
                    if enhanced.get("style_constraints"):
                        plan.style_constraints = enhanced["style_constraints"]
                    if enhanced.get("cta"):
                        plan.cta = enhanced["cta"]
                    if enhanced.get("subject_style"):
                        plan.subject_style = enhanced["subject_style"]
            except Exception as e:
                logger.warning(f"LLM plan enhancement failed: {e}")
        
        return plan
    
    async def _llm_campaign_plan_enhancement(
        self,
        schema: CsvSchemaInference,
        sample_rows: list[dict],
        current_plan: CampaignPlan,
    ) -> dict | None:
        """Use LLM to enhance campaign plan."""
        if not self.llm_client:
            return None
        
        prompt = f"""You are an expert email outreach strategist.

Campaign Goal: {schema.inferred_goal}
Available Personalization: {schema.personalization_columns}
Sample Recipients: {json.dumps(sample_rows[:2], indent=2)}

Review this draft campaign plan and suggest improvements:

Tone: {current_plan.tone}
CTA: {current_plan.cta}
Subject Style: {current_plan.subject_style}
Style Constraints: {current_plan.style_constraints}

Provide suggestions as JSON:
- tone: improved tone suggestion
- cta: improved call-to-action
- subject_style: improved subject style
- style_constraints: list of specific constraints

Respond with only valid JSON."""
        
        try:
            from langchain_core.messages import HumanMessage
            response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
            content = response.content
            
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            return json.loads(content.strip())
        except Exception as e:
            logger.error(f"LLM plan enhancement error: {e}")
            return None
