"""Recipient graph nodes."""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import mask_sensitive_data
from app.db.models import (
    Campaign,
    CampaignRow,
    EmailDraft,
    GmailAccount,
    RowStatus,
    SendEvent,
    SendStatus,
)
from app.graphs.state import RecipientGraphState
from app.services.csv_loader import CSVLoader, DataLoader
from app.services.csv_profiler import CSVProfiler
from app.services.draft_generation_service import DraftGenerationService
from app.services.gmail_client import GmailClient, dict_to_credentials
from app.services.idempotency_service import IdempotencyService
from app.services.send_service import SendService
from app.services.validation_service import ValidationService

logger = get_logger(__name__)


class RecipientGraphNodes:
    """Nodes for the recipient graph."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.draft_service = DraftGenerationService()
        self.validation_service = ValidationService()
        self.send_service = SendService()
        self.idempotency_service = IdempotencyService()
    
    async def normalize_row(self, state: RecipientGraphState) -> RecipientGraphState:
        """Normalize row data."""
        logger.info(f"Normalizing row {state.row_number} for campaign {state.campaign_id}")
        
        try:
            row_data = state.raw_row
            
            # Basic normalization
            normalized = {}
            for key, value in row_data.items():
                # Strip whitespace
                normalized[key] = str(value).strip() if value is not None else ""
            
            state.normalized_row = normalized
            state.status = "normalized"
            
        except Exception as e:
            logger.error(f"Failed to normalize row: {e}")
            state.errors.append(f"Normalization error: {str(e)}")
        
        return state
    
    async def eligibility_check(self, state: RecipientGraphState) -> RecipientGraphState:
        """Check if row is eligible to receive email."""
        logger.info(f"Checking eligibility for row {state.recipient_id}")
        
        try:
            # Get campaign and schema
            campaign = await self.session.get(Campaign, state.campaign_id)
            if not campaign:
                state.status = "ineligible"
                state.eligibility = {"eligible": False, "reason": "Campaign not found"}
                return state
            
            schema = campaign.inferred_schema_json
            if not schema:
                state.status = "ineligible"
                state.eligibility = {"eligible": False, "reason": "Schema not available"}
                return state
            
            # Check blocker rules
            blockers = schema.get("blocker_rules", [])
            normalized = state.normalized_row or {}
            
            eligibility = {"eligible": True, "reason": None, "blockers_checked": []}
            
            for rule in blockers:
                column = rule.get("column")
                operator = rule.get("operator")
                value = rule.get("value")
                
                if column not in normalized:
                    continue
                
                cell_value = normalized[column].lower().strip()
                blocked = False
                
                if operator == "truthy":
                    blocked = cell_value in ("true", "yes", "1", "y", "t")
                elif operator == "eq":
                    blocked = cell_value == str(value).lower()
                elif operator == "neq":
                    blocked = cell_value != str(value).lower()
                elif operator == "is_null":
                    blocked = not cell_value
                elif operator == "not_null":
                    blocked = bool(cell_value)
                
                if blocked:
                    eligibility["eligible"] = False
                    eligibility["reason"] = rule.get("reason", f"Blocked by {column}")
                    eligibility["blockers_checked"].append({
                        "column": column,
                        "value": normalized[column],
                        "rule": rule,
                    })
                    break
            
            # Check for valid email
            recipient_email = normalized.get(schema.get("primary_email_column", ""), "")
            if not recipient_email:
                eligibility["eligible"] = False
                eligibility["reason"] = "No email address found"
            
            state.eligibility = eligibility
            
            if not eligibility["eligible"]:
                state.status = "ineligible"
            
        except Exception as e:
            logger.error(f"Eligibility check failed: {e}")
            state.errors.append(f"Eligibility error: {str(e)}")
        
        return state
    
    async def build_personalization_context(self, state: RecipientGraphState) -> RecipientGraphState:
        """Build personalization context for the row."""
        logger.info(f"Building personalization context for row {state.recipient_id}")
        
        try:
            campaign = await self.session.get(Campaign, state.campaign_id)
            if not campaign:
                return state
            
            schema = campaign.inferred_schema_json
            normalized = state.normalized_row or {}
            
            context = {}
            
            # Add name fields
            name_cols = schema.get("recipient_name_columns", [])
            name_parts = []
            for col in name_cols:
                if col in normalized and normalized[col]:
                    name_parts.append(normalized[col])
                    context[col] = normalized[col]
            
            if name_parts:
                context["first_name"] = name_parts[0]
                context["full_name"] = " ".join(name_parts)
            
            # Add company
            company_cols = schema.get("company_columns", [])
            for col in company_cols:
                if col in normalized and normalized[col]:
                    context["company"] = normalized[col]
                    break
            
            # Add personalization fields
            for col in schema.get("personalization_columns", []):
                if col in normalized and normalized[col]:
                    context[col] = normalized[col]
            
            state.personalization_context = context
            
        except Exception as e:
            logger.error(f"Failed to build personalization context: {e}")
            state.errors.append(f"Personalization context error: {str(e)}")
        
        return state
    
    async def generate_email_draft(self, state: RecipientGraphState) -> RecipientGraphState:
        """Generate email draft for the row."""
        logger.info(f"Generating email draft for row {state.recipient_id}")
        
        try:
            campaign = await self.session.get(Campaign, state.campaign_id)
            if not campaign:
                state.errors.append("Campaign not found")
                return state
            
            from app.schemas.csv_inference import CsvSchemaInference, CampaignPlan
            
            schema = CsvSchemaInference(**campaign.inferred_schema_json)
            plan = CampaignPlan(**campaign.campaign_plan_json)
            
            # Get sender name from Gmail account if available
            sender_name = None
            if campaign.gmail_account_id:
                gmail_account = await self.session.get(GmailAccount, campaign.gmail_account_id)
                if gmail_account:
                    sender_name = gmail_account.sender_name
                    logger.debug(f"Using sender name for recipient draft: {sender_name}")
            
            # Generate draft with sender name
            draft = await self.draft_service.generate_draft(
                schema, plan, state.normalized_row or {}, sender_name
            )
            
            state.generated_email = draft.model_dump()
            state.status = "generated"
            
            # Save to DB
            campaign_row = await self.session.get(CampaignRow, state.recipient_id)
            if campaign_row:
                email_draft = EmailDraft(
                    campaign_row_id=state.recipient_id,
                    subject=draft.subject,
                    plain_text_body=draft.plain_text_body,
                    html_body=draft.html_body,
                    personalization_fields_used=draft.personalization_fields_used,
                    key_claims_used=draft.key_claims_used,
                    generation_confidence=int(draft.confidence * 100),
                    needs_human_review=draft.needs_human_review,
                    review_reasons=draft.review_reasons,
                )
                self.session.add(email_draft)
                await self.session.commit()
            
        except Exception as e:
            logger.error(f"Failed to generate draft: {e}")
            state.errors.append(f"Draft generation error: {str(e)}")
        
        return state
    
    async def validate_email_draft(self, state: RecipientGraphState) -> RecipientGraphState:
        """Validate generated email draft."""
        logger.info(f"Validating email draft for row {state.recipient_id}")
        
        try:
            if not state.generated_email:
                state.errors.append("No draft to validate")
                return state
            
            from app.schemas.draft import GeneratedEmail
            
            draft = GeneratedEmail(**state.generated_email)
            
            # Get recipient email
            campaign = await self.session.get(Campaign, state.campaign_id)
            schema = campaign.inferred_schema_json if campaign else {}
            email_col = schema.get("primary_email_column", "")
            recipient_email = (state.normalized_row or {}).get(email_col, "")
            
            # Validate
            report = self.validation_service.validate_draft(
                draft, recipient_email, state.normalized_row or {}
            )
            
            state.validation_report = report.model_dump()
            
            # Update DB
            campaign_row = await self.session.get(CampaignRow, state.recipient_id)
            if campaign_row:
                campaign_row.validation_report_json = state.validation_report
                await self.session.commit()
            
            # Determine status
            if not report.passed:
                state.status = "awaiting_review"
                state.review_required = True
            elif report.requires_human_review:
                state.status = "awaiting_review"
                state.review_required = True
            else:
                state.status = "validated"
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            state.errors.append(f"Validation error: {str(e)}")
        
        return state
    
    async def row_review_interrupt_if_needed(self, state: RecipientGraphState) -> RecipientGraphState:
        """Handle row-level review if required."""
        logger.info(f"Checking if row review needed for {state.recipient_id}")
        
        # If review is not required, mark as not_required and proceed
        if not state.review_required:
            state.approval_status = "not_required"
            logger.info(f"Row {state.recipient_id} does not require review, proceeding to send")
            return state
        
        # This node pauses for human review
        # Resume happens when approval is received
        
        if state.approval_status == "approved":
            state.status = "sending"
        elif state.approval_status == "rejected":
            state.status = "skipped"
        
        return state
    
    async def send_email_via_gmail(self, state: RecipientGraphState) -> RecipientGraphState:
        """Send email via Gmail."""
        logger.info(f"[SEND] Starting email send for row {state.recipient_id}")
        
        try:
            # Get campaign row and draft
            campaign_row = await self.session.get(CampaignRow, state.recipient_id)
            if not campaign_row:
                logger.error(f"[SEND] Campaign row not found: {state.recipient_id}")
                state.errors.append("Campaign row not found")
                state.status = "failed"
                return state
            
            logger.info(f"[SEND] Found campaign row: {campaign_row.id}, email: {mask_sensitive_data(campaign_row.recipient_email or '', 3)}")
            
            # Get email draft
            from sqlalchemy import select
            result = await self.session.execute(
                select(EmailDraft).where(EmailDraft.campaign_row_id == state.recipient_id)
            )
            draft = result.scalar_one_or_none()
            
            if not draft:
                logger.error(f"[SEND] Email draft not found for row: {state.recipient_id}")
                state.errors.append("Email draft not found")
                state.status = "failed"
                return state
            
            logger.info(f"[SEND] Found email draft: {draft.id}, subject: {draft.subject[:50]}...")
            
            # Get campaign and Gmail account
            campaign = await self.session.get(Campaign, state.campaign_id)
            if not campaign:
                logger.error(f"[SEND] Campaign not found: {state.campaign_id}")
                state.errors.append("Campaign not found")
                state.status = "failed"
                return state
            
            logger.info(f"[SEND] Found campaign: {campaign.id}, gmail_account_id: {campaign.gmail_account_id}, dry_run: {campaign.dry_run}")
            
            if not campaign.gmail_account_id:
                logger.error(f"[SEND] Gmail account not connected for campaign: {state.campaign_id}")
                state.errors.append("Gmail account not connected")
                state.status = "failed"
                return state
            
            gmail_account = await self.session.get(GmailAccount, campaign.gmail_account_id)
            if not gmail_account:
                logger.error(f"[SEND] Gmail account not found: {campaign.gmail_account_id}")
                state.errors.append("Gmail account not found")
                state.status = "failed"
                return state
            
            logger.info(f"[SEND] Found Gmail account: {gmail_account.email}, sender_name: {gmail_account.sender_name}")
            
            # Check idempotency
            from app.core.security import generate_idempotency_key
            
            idempotency_key = generate_idempotency_key(
                campaign_id=state.campaign_id,
                recipient_email=campaign_row.recipient_email or "",
                subject=draft.subject,
                body=draft.plain_text_body,
            )
            
            logger.info(f"[SEND] Checking idempotency with key: {idempotency_key[:20]}...")
            
            existing = await self.idempotency_service.check_duplicate(
                self.session, state.campaign_id,
                campaign_row.recipient_email or "",
                draft.subject, draft.plain_text_body
            )
            
            if existing and existing.status == SendStatus.SENT:
                logger.info(f"[SEND] Duplicate found, already sent: {existing.id}")
                state.send_result = {
                    "success": True,
                    "duplicate": True,
                    "message": "Email already sent",
                    "send_event_id": existing.id,
                }
                state.status = "sent"
                
                # Update DB
                campaign_row.status = RowStatus.SENT
                await self.session.commit()
                
                return state
            
            # Check if dry-run mode
            if state.dry_run or campaign.dry_run:
                logger.info(f"[SEND] Dry-run mode, skipping actual send. state.dry_run={state.dry_run}, campaign.dry_run={campaign.dry_run}")
                # Record as dry-run preview
                send_event = await self.idempotency_service.record_send_attempt(
                    session=self.session,
                    campaign_row_id=state.recipient_id,
                    campaign_id=state.campaign_id,
                    recipient_email=campaign_row.recipient_email or "",
                    subject=draft.subject,
                    body=draft.plain_text_body,
                    status=SendStatus.DRY_RUN,
                    provider_response={"dry_run": True},
                )
                
                state.send_result = {
                    "success": True,
                    "dry_run": True,
                    "message": "Email recorded in dry-run mode",
                    "send_event_id": send_event.id,
                }
                state.status = "dry_run_preview"
                
                # Update DB
                campaign_row.status = RowStatus.SENT  # Mark as processed
                await self.session.commit()
                
                return state
            
            # Actually send
            try:
                from app.core.security import decrypt_token
                
                logger.info(f"[SEND] Decrypting token for Gmail account: {gmail_account.email}")
                
                # Decrypt token
                if not gmail_account.token_encrypted:
                    logger.error(f"[SEND] No encrypted token found for Gmail account: {gmail_account.email}")
                    raise ValueError("Gmail token not found or expired. Please reconnect your Gmail account.")
                
                token_data = json.loads(decrypt_token(gmail_account.token_encrypted))
                logger.info(f"[SEND] Token decrypted successfully, expires at: {token_data.get('expiry', 'unknown')}")
                
                # Create Gmail client
                logger.info(f"[SEND] Creating Gmail client...")
                client = GmailClient(token_data)
                
                # Send email
                logger.info(f"[SEND] Sending email via Gmail API...")
                logger.info(f"[SEND]   From: {gmail_account.email}")
                logger.info(f"[SEND]   To: {mask_sensitive_data(campaign_row.recipient_email or '', 3)}")
                logger.info(f"[SEND]   Subject: {draft.subject[:50]}...")
                
                result = client.send_email(
                    sender=gmail_account.email,
                    to=campaign_row.recipient_email or "",
                    subject=draft.subject,
                    plain_text=draft.plain_text_body,
                    html_body=draft.html_body,
                )
                
                logger.info(f"[SEND] Email sent successfully! Message ID: {result.get('message_id')}")
                
                # Record success
                send_event = await self.idempotency_service.record_send_attempt(
                    session=self.session,
                    campaign_row_id=state.recipient_id,
                    campaign_id=state.campaign_id,
                    recipient_email=campaign_row.recipient_email or "",
                    subject=draft.subject,
                    body=draft.plain_text_body,
                    status=SendStatus.SENT,
                    provider_response=result,
                )
                
                state.send_result = {
                    "success": True,
                    "message_id": result.get("message_id"),
                    "send_event_id": send_event.id,
                }
                state.status = "sent"
                
                # Update DB
                campaign_row.status = RowStatus.SENT
                await self.session.commit()
                
                logger.info(
                    "[SEND] Email sent successfully",
                    recipient=mask_sensitive_data(campaign_row.recipient_email or "", 3),
                    message_id=result.get("message_id"),
                )
                
            except Exception as e:
                # Handle specific token decryption errors
                from cryptography.fernet import InvalidToken
                if isinstance(e, InvalidToken):
                    logger.error(f"[SEND] Token decryption failed - encryption key may have changed. User needs to reconnect Gmail account.")
                    error_msg = "Gmail authentication token is invalid. Please go to Settings and reconnect your Gmail account."
                else:
                    logger.error(f"[SEND] Gmail send failed with error: {type(e).__name__}: {str(e)}")
                    import traceback
                    logger.error(f"[SEND] Stack trace: {traceback.format_exc()}")
                    error_msg = f"Failed to send: {str(e)}"
                
                # Record failure
                send_event = await self.idempotency_service.record_send_attempt(
                    session=self.session,
                    campaign_row_id=state.recipient_id,
                    campaign_id=state.campaign_id,
                    recipient_email=campaign_row.recipient_email or "",
                    subject=draft.subject,
                    body=draft.plain_text_body,
                    status=SendStatus.FAILED,
                    error_message=error_msg,
                )
                
                state.send_result = {
                    "success": False,
                    "error": error_msg,
                    "error_type": type(e).__name__,
                    "send_event_id": send_event.id,
                }
                state.status = "failed"
                state.errors.append(f"Send failed: {error_msg}")
                
                # Update DB
                campaign_row.status = RowStatus.FAILED
                campaign_row.error_message = error_msg
                await self.session.commit()
        
        except Exception as e:
            logger.error(f"[SEND] Send process failed: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"[SEND] Stack trace: {traceback.format_exc()}")
            state.errors.append(f"Send process error: {str(e)}")
            state.status = "failed"
        
        logger.info(f"[SEND] Final status for row {state.recipient_id}: {state.status}")
        return state
    
    async def persist_send_outcome(self, state: RecipientGraphState) -> RecipientGraphState:
        """Persist final send outcome."""
        logger.info(f"Persisting outcome for row {state.recipient_id}")
        
        # Outcome already persisted in send node
        # This node is for any final cleanup
        
        return state
