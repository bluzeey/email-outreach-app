"""Campaign graph definition."""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import settings
from app.core.logging import get_logger
from app.graphs.nodes.campaign_nodes import CampaignGraphNodes
from app.graphs.state import CampaignGraphState

logger = get_logger(__name__)


def create_campaign_graph(session):
    """Create campaign graph."""
    
    nodes = CampaignGraphNodes(session)
    
    # Define graph
    workflow = StateGraph(CampaignGraphState)
    
    # Add nodes
    workflow.add_node("load_csv", nodes.load_csv)
    workflow.add_node("profile_csv", nodes.profile_csv)
    workflow.add_node("infer_schema", nodes.infer_schema)
    workflow.add_node("infer_campaign_plan", nodes.infer_campaign_plan)
    workflow.add_node("generate_sample_drafts", nodes.generate_sample_drafts)
    workflow.add_node("campaign_review_interrupt", nodes.campaign_review_interrupt)
    workflow.add_node("prepare_recipient_records", nodes.prepare_recipient_records)
    workflow.add_node("dispatch_recipient_runs", nodes.dispatch_recipient_runs)
    workflow.add_node("aggregate_progress", nodes.aggregate_progress)
    workflow.add_node("finalize_campaign", nodes.finalize_campaign)
    
    # Define edges
    workflow.set_entry_point("load_csv")
    workflow.add_edge("load_csv", "profile_csv")
    workflow.add_edge("profile_csv", "infer_schema")
    workflow.add_edge("infer_schema", "infer_campaign_plan")
    workflow.add_edge("infer_campaign_plan", "generate_sample_drafts")
    workflow.add_edge("generate_sample_drafts", "campaign_review_interrupt")
    
    # Review interrupt - conditional
    def review_router(state: CampaignGraphState):
        if state.approval_status == "approved":
            return "prepare_recipient_records"
        elif state.approval_status == "rejected":
            return "finalize_campaign"
        else:
            return "campaign_review_interrupt"  # Loop back to wait
    
    workflow.add_conditional_edges(
        "campaign_review_interrupt",
        review_router,
        {
            "prepare_recipient_records": "prepare_recipient_records",
            "finalize_campaign": "finalize_campaign",
            "campaign_review_interrupt": "campaign_review_interrupt",
        }
    )
    
    workflow.add_edge("prepare_recipient_records", "dispatch_recipient_runs")
    workflow.add_edge("dispatch_recipient_runs", "aggregate_progress")
    
    # Progress aggregation - loop until complete
    def progress_router(state: CampaignGraphState):
        if state.status == "completed":
            return "finalize_campaign"
        elif state.status == "cancelled":
            return "finalize_campaign"
        elif state.status == "paused":
            return END
        else:
            return "dispatch_recipient_runs"  # Continue processing
    
    workflow.add_conditional_edges(
        "aggregate_progress",
        progress_router,
        {
            "finalize_campaign": "finalize_campaign",
            "dispatch_recipient_runs": "dispatch_recipient_runs",
        }
    )
    
    workflow.add_edge("finalize_campaign", END)
    
    # Compile with checkpointing
    # Convert checkpoint URL for aiosqlite
    checkpoint_url = settings.CHECKPOINT_DATABASE_URL
    if checkpoint_url.startswith("sqlite:///"):
        checkpoint_url = checkpoint_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    
    try:
        checkpointer = SqliteSaver.from_conn_string(
            checkpoint_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        )
        return workflow.compile(checkpointer=checkpointer)
    except Exception as e:
        logger.warning(f"Failed to setup checkpointing: {e}, running without persistence")
        return workflow.compile()


def get_campaign_thread_id(campaign_id: str) -> str:
    """Get deterministic thread ID for campaign."""
    return f"campaign:{campaign_id}"
