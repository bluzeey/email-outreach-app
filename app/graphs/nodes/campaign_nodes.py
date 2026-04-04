"""Campaign graph nodes."""

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import CampaignRow, RowStatus
from app.graphs.state import CampaignGraphState
from app.services.csv_loader import CSVLoader, DataLoader
from app.services.csv_profiler import CSVProfiler
from app.services.progress_manager import progress_manager
from app.services.schema_inference_service import SchemaInferenceService

logger = get_logger(__name__)


class CampaignGraphNodes:
    """Nodes for the campaign graph."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.schema_service = SchemaInferenceService()
    
    async def load_csv(self, state: CampaignGraphState) -> CampaignGraphState:
        """Load CSV file."""
        logger.info(f"Loading CSV for campaign {state.campaign_id}")
        
        try:
            # CSV path should already be in state from API endpoint
            if not state.csv_path:
                state.errors.append("CSV path not found in state")
                state.status = "failed"
                return state
            
            state.status = "profiling"
            
        except Exception as e:
            logger.error(f"Failed to load CSV: {e}")
            state.errors.append(f"CSV load error: {str(e)}")
            state.status = "failed"
        
        return state
    
    async def profile_csv(self, state: CampaignGraphState) -> CampaignGraphState:
        """Profile CSV columns and types."""
        logger.info(f"Profiling CSV for campaign {state.campaign_id}")
        
        # Emit progress
        await progress_manager.update(
            state.campaign_id,
            status="processing",
            message="Loading and profiling CSV data...",
            stage="profiling",
            percent_complete=5,
        )
        
        try:
            df = DataLoader.load_file(state.csv_path)
            profile = CSVProfiler.profile_csv(df)
            
            state.csv_profile = profile.model_dump()
            
            # Store row IDs for later dispatch
            sample_rows = CSVProfiler.get_sample_rows(df, len(df))
            
        except Exception as e:
            logger.error(f"Failed to profile CSV: {e}")
            state.errors.append(f"CSV profiling error: {str(e)}")
            state.status = "failed"
        
        return state
    
    async def infer_schema(self, state: CampaignGraphState) -> CampaignGraphState:
        """Infer CSV schema and semantics."""
        logger.info(f"Inferring schema for campaign {state.campaign_id}")
        
        # Emit progress
        await progress_manager.update(
            state.campaign_id,
            status="processing",
            message="Inferring schema from CSV data...",
            stage="infer_schema",
            percent_complete=10,
        )
        
        try:
            # Load sample rows
            df = DataLoader.load_file(state.csv_path)
            sample_rows = CSVProfiler.get_sample_rows(df, 5)
            
            # Create profile from state
            profile_data = state.csv_profile
            from app.schemas.csv_inference import CsvProfile, CsvColumnProfile
            columns = [CsvColumnProfile(**c) for c in profile_data["columns"]]
            profile = CsvProfile(
                total_rows=profile_data["total_rows"],
                total_columns=profile_data["total_columns"],
                columns=columns,
                column_names=profile_data["column_names"],
            )
            
            # Infer schema
            inference = await self.schema_service.infer_schema(profile, sample_rows)
            
            state.inferred_schema = inference.model_dump()
            state.schema_confidence = inference.confidence
            
            # Emit progress
            await progress_manager.update(
                state.campaign_id,
                status="processing",
                message=f"Schema inferred with {int(inference.confidence * 100)}% confidence",
                stage="infer_schema",
                percent_complete=20,
            )
            
            # Check if review needed
            if inference.confidence < 0.7 or inference.unresolved_questions:
                state.status = "awaiting_schema_review"
            else:
                state.status = "awaiting_campaign_approval"
            
        except Exception as e:
            logger.error(f"Failed to infer schema: {e}")
            state.errors.append(f"Schema inference error: {str(e)}")
            state.status = "awaiting_schema_review"  # Require manual review
        
        return state
    
    async def infer_campaign_plan(self, state: CampaignGraphState) -> CampaignGraphState:
        """Generate campaign plan from schema."""
        logger.info(f"Generating campaign plan for {state.campaign_id}")
        
        # Emit progress
        await progress_manager.update(
            state.campaign_id,
            status="processing",
            message="Generating campaign plan...",
            stage="generate_plan",
            percent_complete=30,
        )
        
        try:
            from app.schemas.csv_inference import CsvSchemaInference
            
            schema = CsvSchemaInference(**state.inferred_schema)
            
            # Load sample rows
            df = DataLoader.load_file(state.csv_path)
            sample_rows = CSVProfiler.get_sample_rows(df, 5)
            
            # Generate plan
            plan = await self.schema_service.generate_campaign_plan(schema, sample_rows)
            
            state.campaign_plan = plan.model_dump()
            
            # Emit progress
            await progress_manager.update(
                state.campaign_id,
                status="processing",
                message=f"Campaign plan generated: {plan.inferred_goal}",
                stage="generate_plan",
                percent_complete=40,
            )
            
        except Exception as e:
            logger.error(f"Failed to generate campaign plan: {e}")
            state.errors.append(f"Campaign plan error: {str(e)}")
        
        return state
    
    async def generate_sample_drafts(self, state: CampaignGraphState) -> CampaignGraphState:
        """Generate sample drafts for review."""
        logger.info(f"Generating sample drafts for {state.campaign_id}")
        
        # Emit progress
        await progress_manager.update(
            state.campaign_id,
            status="processing",
            message="Generating sample email drafts...",
            stage="generate_drafts",
            percent_complete=50,
        )
        
        try:
            from app.schemas.csv_inference import CsvSchemaInference, CampaignPlan
            from app.services.draft_generation_service import DraftGenerationService
            
            schema = CsvSchemaInference(**state.inferred_schema)
            plan = CampaignPlan(**state.campaign_plan)
            
            # Load sample rows
            df = DataLoader.load_file(state.csv_path)
            sample_rows = CSVProfiler.get_sample_rows(df, 3)
            
            # Generate drafts
            draft_service = DraftGenerationService()
            drafts = await draft_service.generate_sample_drafts(schema, plan, sample_rows, 3)
            
            state.sample_drafts = [d.model_dump() for d in drafts]
            logger.info(f"Generated {len(drafts)} sample drafts")
            
            # Emit progress
            await progress_manager.update(
                state.campaign_id,
                status="processing",
                message=f"Generated {len(drafts)} sample drafts",
                stage="generate_drafts",
                percent_complete=60,
            )
            
        except Exception as e:
            logger.error(f"Failed to generate sample drafts: {e}")
            state.errors.append(f"Sample draft error: {str(e)}")
        
        return state
    
    async def await_approval_status(self, state: CampaignGraphState) -> CampaignGraphState:
        """Set campaign status to awaiting approval and end.
        
        This node completes the analysis phase. Human approval will be handled
        via separate API call, not via graph execution.
        """
        logger.info(f"Analysis complete for campaign {state.campaign_id}, awaiting approval")
        
        # Verify recipients were created
        from sqlalchemy import func, select
        
        try:
            recipient_count_result = await self.session.execute(
                select(func.count(CampaignRow.id)).where(CampaignRow.campaign_id == state.campaign_id)
            )
            recipient_count = recipient_count_result.scalar() or 0
            
            logger.info(f"Verification: {recipient_count} recipient rows exist for campaign {state.campaign_id}")
            
            if recipient_count == 0:
                error_msg = "No recipient rows were created during analysis"
                logger.error(error_msg)
                state.errors.append(error_msg)
                state.status = "failed"
            else:
                state.totals = state.totals or {}
                state.totals["total_rows"] = recipient_count
                
        except Exception as e:
            logger.error(f"Failed to verify recipient count: {e}")
        
        # Determine final status based on schema confidence and errors
        if state.status == "failed":
            # Already marked as failed (no recipients created)
            pass
        elif state.schema_confidence and state.schema_confidence < 0.7:
            state.status = "awaiting_schema_review"
        else:
            state.status = "awaiting_campaign_approval"
        
        state.approval_status = "pending"
        logger.info(f"Final status: {state.status} with {state.totals.get('total_rows', 0)} recipients")
        
        return state
    
    async def prepare_recipient_records(self, state: CampaignGraphState) -> CampaignGraphState:
        """Create recipient records from CSV and generate email drafts for preview."""
        logger.info(f"Preparing recipient records for {state.campaign_id}")
        
        try:
            # Load CSV directly from state (no need to fetch Campaign)
            if not state.csv_path:
                state.errors.append("No CSV path in state")
                logger.error(f"No CSV path for campaign {state.campaign_id}")
                return state
            
            logger.info(f"Loading file: {state.csv_path}")
            df = DataLoader.load_file(state.csv_path)
            logger.info(f"Loaded {len(df)} rows from file")
            
            # Get schema and plan
            from app.schemas.csv_inference import CsvSchemaInference, CampaignPlan
            from app.services.draft_generation_service import DraftGenerationService
            from app.db.models import EmailDraft
            
            schema = CsvSchemaInference(**state.inferred_schema)
            logger.info(f"Schema loaded - primary_email_column: {schema.primary_email_column}")
            
            # Handle missing campaign plan - create default plan
            if state.campaign_plan and isinstance(state.campaign_plan, dict):
                plan = CampaignPlan(**state.campaign_plan)
                logger.info(f"Campaign plan loaded: {plan.inferred_goal}")
            else:
                # Create a default plan if generation failed
                plan = CampaignPlan(
                    campaign_name="Outreach Campaign",
                    inferred_goal="Connect with recipients",
                    target_audience="General audience",
                    tone="professional",
                    cta="Looking forward to hearing from you",
                    style_constraints=["Keep it brief"],
                    do_not_claim=["Don't make false claims"],
                    subject_style="direct",
                    personalization_priority=[],
                    review_policy={},
                    sending_policy={}
                )
                logger.warning(f"Using default campaign plan for {state.campaign_id}")
            
            draft_service = DraftGenerationService()
            
            # Create rows - skip rows without valid email
            row_ids = []
            skipped_count = 0
            draft_errors = 0
            created_count = 0
            
            logger.info(f"Starting to process {len(df)} rows...")
            
            # Emit initial progress
            await progress_manager.update(
                state.campaign_id,
                status="processing",
                message=f"Processing {len(df)} rows...",
                stage="processing",
                total_rows=len(df),
                processed_rows=0,
                current_row=None,
            )
            
            for idx in range(len(df)):
                row_data = DataLoader.get_row_as_dict(df, idx)
                
                # Extract and validate email
                recipient_email = row_data.get(schema.primary_email_column, "")
                recipient_email = str(recipient_email).strip() if recipient_email else ""
                
                # Skip rows without valid email
                if not recipient_email or recipient_email.lower() in ["nan", "null", "none", "", "n/a"]:
                    skipped_count += 1
                    continue
                
                # Emit progress every 10 rows
                if created_count > 0 and created_count % 10 == 0:
                    await progress_manager.update(
                        state.campaign_id,
                        status="processing",
                        message=f"Processing row {idx + 1} of {len(df)}...",
                        stage="processing",
                        total_rows=len(df),
                        processed_rows=created_count,
                        current_row=idx + 1,
                    )
                
                # Create recipient record FIRST (before draft generation)
                # This ensures rows exist even if draft generation fails
                try:
                    campaign_row = CampaignRow(
                        campaign_id=state.campaign_id,
                        row_number=idx + 1,
                        raw_row_json=row_data,
                        recipient_email=recipient_email,
                        status=RowStatus.QUEUED,
                    )
                    
                    self.session.add(campaign_row)
                    await self.session.flush()
                    row_ids.append(campaign_row.id)
                    created_count += 1
                    
                    logger.debug(f"Created row {idx + 1} with email {recipient_email}")
                except Exception as row_error:
                    logger.error(f"Failed to create row {idx + 1}: {row_error}")
                    state.errors.append(f"Row {idx + 1} creation error: {str(row_error)}")
                    continue
                
                # Generate email draft for preview (AFTER row is created)
                try:
                    draft = await draft_service.generate_draft(schema, plan, row_data)
                    
                    # Save draft to DB
                    email_draft = EmailDraft(
                        campaign_row_id=campaign_row.id,
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
                    
                    # Update row status to show draft is ready
                    campaign_row.status = RowStatus.GENERATED
                    
                except Exception as draft_error:
                    logger.error(f"Failed to generate draft for row {campaign_row.id}: {draft_error}")
                    draft_errors += 1
                    campaign_row.error_message = f"Draft generation failed: {str(draft_error)}"
                    # Keep row as QUEUED so it can be retried
                
                # Flush every 10 rows to avoid memory issues (commit happens at API level)
                if len(row_ids) % 10 == 0:
                    try:
                        await self.session.flush()
                        logger.info(f"Flushed batch of 10 rows (total created: {created_count})")
                    except Exception as flush_error:
                        logger.error(f"Failed to flush batch: {flush_error}")
                        state.errors.append(f"Flush error: {str(flush_error)}")
            
            # Final flush for remaining rows
            try:
                await self.session.flush()
                logger.info(f"Final flush - created {created_count} recipient records")
            except Exception as final_flush_error:
                logger.error(f"Failed to finalize flush: {final_flush_error}")
                state.errors.append(f"Final flush error: {str(final_flush_error)}")
            
            state.row_ids = row_ids
            state.totals = {
                "total_rows": len(row_ids),
                "skipped_no_email": skipped_count,
                "draft_errors": draft_errors,
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "skipped": 0,
            }
            
            logger.info(f"Created {len(row_ids)} recipient records with drafts (skipped {skipped_count} without email, {draft_errors} draft errors)")
            
            # Add error summary if there were issues
            if state.errors:
                logger.warning(f"Completed with {len(state.errors)} errors: {state.errors}")
            
        except Exception as e:
            logger.error(f"Failed to prepare recipients: {e}")
            state.errors.append(f"Recipient preparation error: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        
        return state
    
    async def dispatch_recipient_runs(self, state: CampaignGraphState) -> CampaignGraphState:
        """Dispatch recipient processing jobs."""
        logger.info(f"Dispatching recipient runs for {state.campaign_id}")
        
        # In a real implementation, this would queue jobs
        # For now, we mark that dispatch has started
        
        state.dispatch_cursor = len(state.row_ids)
        
        return state
    
    async def aggregate_progress(self, state: CampaignGraphState) -> CampaignGraphState:
        """Aggregate progress from recipient runs."""
        logger.info(f"Aggregating progress for {state.campaign_id}")
        
        try:
            # Query current status from DB
            from sqlalchemy import func, select
            
            result = await self.session.execute(
                select(
                    CampaignRow.status,
                    func.count(CampaignRow.id).label("count")
                )
                .where(CampaignRow.campaign_id == state.campaign_id)
                .group_by(CampaignRow.status)
            )
            
            status_counts = {row.status.value: row.count for row in result}
            
            state.totals = {
                "total_rows": sum(status_counts.values()),
                "processed": sum(c for s, c in status_counts.items() if s not in ["queued"]),
                "sent": status_counts.get("sent", 0),
                "failed": status_counts.get("failed", 0),
                "skipped": status_counts.get("skipped", 0) + status_counts.get("ineligible", 0),
            }
            
            # Check if complete
            total = state.totals["total_rows"]
            processed = state.totals["processed"]
            
            if total > 0 and processed >= total:
                state.status = "completed"
            
        except Exception as e:
            logger.error(f"Failed to aggregate progress: {e}")
        
        return state
    
    async def finalize_campaign(self, state: CampaignGraphState) -> CampaignGraphState:
        """Finalize campaign."""
        logger.info(f"Finalizing campaign {state.campaign_id}")
        
        # Final cleanup and reporting
        # Campaign table updates happen at API level
        
        return state
