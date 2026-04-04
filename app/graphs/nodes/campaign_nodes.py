"""Campaign graph nodes."""

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Campaign, CampaignRow, CampaignStatus, RowStatus
from app.graphs.state import CampaignGraphState
from app.services.csv_loader import CSVLoader, DataLoader
from app.services.csv_profiler import CSVProfiler
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
            campaign = await self.session.get(Campaign, state.campaign_id)
            if not campaign or not campaign.csv_storage_path:
                state.errors.append("Campaign or CSV not found")
                state.status = "failed"
                return state
            
            state.csv_path = campaign.csv_storage_path
            state.context = campaign.context or ""  # Load campaign context
            state.status = "profiling"
            
        except Exception as e:
            logger.error(f"Failed to load CSV: {e}")
            state.errors.append(f"CSV load error: {str(e)}")
            state.status = "failed"
        
        return state
    
    async def profile_csv(self, state: CampaignGraphState) -> CampaignGraphState:
        """Profile CSV columns and types."""
        logger.info(f"Profiling CSV for campaign {state.campaign_id}")
        
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
            
            # Update campaign in DB
            campaign = await self.session.get(Campaign, state.campaign_id)
            if campaign:
                campaign.inferred_schema_json = state.inferred_schema
                await self.session.commit()
            
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
        
        try:
            from app.schemas.csv_inference import CsvSchemaInference
            
            schema = CsvSchemaInference(**state.inferred_schema)
            
            # Load sample rows
            df = DataLoader.load_file(state.csv_path)
            sample_rows = CSVProfiler.get_sample_rows(df, 5)
            
            # Generate plan
            plan = await self.schema_service.generate_campaign_plan(schema, sample_rows)
            
            state.campaign_plan = plan.model_dump()
            
            # Update campaign in DB
            campaign = await self.session.get(Campaign, state.campaign_id)
            if campaign:
                campaign.campaign_plan_json = state.campaign_plan
                await self.session.commit()
            
        except Exception as e:
            logger.error(f"Failed to generate campaign plan: {e}")
            state.errors.append(f"Campaign plan error: {str(e)}")
        
        return state
    
    async def generate_sample_drafts(self, state: CampaignGraphState) -> CampaignGraphState:
        """Generate sample drafts for review."""
        logger.info(f"Generating sample drafts for {state.campaign_id}")
        
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
            
            # Store sample drafts in database for UI display
            campaign = await self.session.get(Campaign, state.campaign_id)
            if campaign:
                campaign.sample_drafts_json = state.sample_drafts
                await self.session.commit()
                logger.info(f"Stored {len(drafts)} sample drafts in database")
            
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
        
        # Determine final status based on schema confidence
        if state.schema_confidence and state.schema_confidence < 0.7:
            state.status = "awaiting_schema_review"
        else:
            state.status = "awaiting_campaign_approval"
        
        state.approval_status = "pending"
        
        # Update campaign status in database
        try:
            campaign = await self.session.get(Campaign, state.campaign_id)
            if campaign:
                campaign.status = CampaignStatus(state.status)
                await self.session.commit()
                logger.info(f"Updated campaign status to {state.status}")
        except Exception as e:
            logger.error(f"Failed to update campaign status: {e}")
        
        return state
    
    async def prepare_recipient_records(self, state: CampaignGraphState) -> CampaignGraphState:
        """Create recipient records from CSV."""
        logger.info(f"Preparing recipient records for {state.campaign_id}")
        
        try:
            campaign = await self.session.get(Campaign, state.campaign_id)
            if not campaign:
                state.errors.append("Campaign not found")
                return state
            
            # Load CSV
            df = DataLoader.load_file(state.csv_path)
            
            # Get schema
            from app.schemas.csv_inference import CsvSchemaInference
            schema = CsvSchemaInference(**state.inferred_schema)
            
            # Create rows
            row_ids = []
            for idx in range(len(df)):
                row_data = DataLoader.get_row_as_dict(df, idx)
                
                # Extract email
                recipient_email = row_data.get(schema.primary_email_column, "")
                
                campaign_row = CampaignRow(
                    campaign_id=state.campaign_id,
                    row_number=idx + 1,
                    raw_row_json=row_data,
                    recipient_email=recipient_email if recipient_email else None,
                    status=RowStatus.QUEUED,
                )
                
                self.session.add(campaign_row)
                await self.session.flush()
                row_ids.append(campaign_row.id)
            
            await self.session.commit()
            
            state.row_ids = row_ids
            state.totals = {
                "total_rows": len(row_ids),
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "skipped": 0,
            }
            
            logger.info(f"Created {len(row_ids)} recipient records")
            
        except Exception as e:
            logger.error(f"Failed to prepare recipients: {e}")
            state.errors.append(f"Recipient preparation error: {str(e)}")
        
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
            
            # Update campaign in DB
            campaign = await self.session.get(Campaign, state.campaign_id)
            if campaign:
                campaign.totals_json = state.totals
                await self.session.commit()
            
        except Exception as e:
            logger.error(f"Failed to aggregate progress: {e}")
        
        return state
    
    async def finalize_campaign(self, state: CampaignGraphState) -> CampaignGraphState:
        """Finalize campaign."""
        logger.info(f"Finalizing campaign {state.campaign_id}")
        
        # Final cleanup and reporting
        
        # Update campaign status in DB
        campaign = await self.session.get(Campaign, state.campaign_id)
        if campaign:
            campaign.status = CampaignStatus(state.status)
            campaign.totals_json = state.totals
            await self.session.commit()
        
        return state
