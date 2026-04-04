"""Campaign graph definition."""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import settings
from app.core.logging import get_logger
from app.graphs.nodes.campaign_nodes import CampaignGraphNodes
from app.graphs.state import CampaignGraphState

logger = get_logger(__name__)


def create_campaign_graph(session):
    """Create campaign graph for analysis phase only.
    
    The graph completes the analysis and stops, waiting for human approval
    via a separate API call. This avoids infinite recursion.
    """
    
    nodes = CampaignGraphNodes(session)
    
    # Define graph
    workflow = StateGraph(CampaignGraphState)
    
    # Add nodes - only up to sample drafts generation
    workflow.add_node("load_csv", nodes.load_csv)
    workflow.add_node("profile_csv", nodes.profile_csv)
    workflow.add_node("infer_schema", nodes.infer_schema)
    workflow.add_node("infer_campaign_plan", nodes.infer_campaign_plan)
    workflow.add_node("generate_sample_drafts", nodes.generate_sample_drafts)
    workflow.add_node("prepare_recipients", nodes.prepare_recipient_records)
    workflow.add_node("await_approval", nodes.await_approval_status)
    
    # Define edges - linear flow ending at await_approval
    workflow.set_entry_point("load_csv")
    workflow.add_edge("load_csv", "profile_csv")
    workflow.add_edge("profile_csv", "infer_schema")
    workflow.add_edge("infer_schema", "infer_campaign_plan")
    workflow.add_edge("infer_campaign_plan", "generate_sample_drafts")
    workflow.add_edge("generate_sample_drafts", "prepare_recipients")
    workflow.add_edge("prepare_recipients", "await_approval")
    workflow.add_edge("await_approval", END)
    
    # Compile with checkpointing
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
