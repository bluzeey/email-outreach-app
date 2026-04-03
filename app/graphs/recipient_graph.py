"""Recipient graph definition."""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import settings
from app.core.logging import get_logger
from app.graphs.nodes.recipient_nodes import RecipientGraphNodes
from app.graphs.state import RecipientGraphState

logger = get_logger(__name__)


def create_recipient_graph(session):
    """Create recipient graph for processing individual rows."""
    
    nodes = RecipientGraphNodes(session)
    
    # Define graph
    workflow = StateGraph(RecipientGraphState)
    
    # Add nodes
    workflow.add_node("normalize_row", nodes.normalize_row)
    workflow.add_node("eligibility_check", nodes.eligibility_check)
    workflow.add_node("build_personalization_context", nodes.build_personalization_context)
    workflow.add_node("generate_email_draft", nodes.generate_email_draft)
    workflow.add_node("validate_email_draft", nodes.validate_email_draft)
    workflow.add_node("row_review_interrupt_if_needed", nodes.row_review_interrupt_if_needed)
    workflow.add_node("send_email_via_gmail", nodes.send_email_via_gmail)
    workflow.add_node("persist_send_outcome", nodes.persist_send_outcome)
    
    # Define edges
    workflow.set_entry_point("normalize_row")
    workflow.add_edge("normalize_row", "eligibility_check")
    
    # Eligibility check - skip ineligible
    def eligibility_router(state: RecipientGraphState):
        eligibility = state.eligibility or {}
        if eligibility.get("eligible", True):
            return "build_personalization_context"
        else:
            return "persist_send_outcome"  # Skip to end
    
    workflow.add_conditional_edges(
        "eligibility_check",
        eligibility_router,
        {
            "build_personalization_context": "build_personalization_context",
            "persist_send_outcome": "persist_send_outcome",
        }
    )
    
    workflow.add_edge("build_personalization_context", "generate_email_draft")
    workflow.add_edge("generate_email_draft", "validate_email_draft")
    
    # Validation - route based on risk
    def validation_router(state: RecipientGraphState):
        report = state.validation_report or {}
        if not report.get("passed", False) or report.get("requires_human_review", False):
            return "row_review_interrupt_if_needed"
        else:
            return "send_email_via_gmail"
    
    workflow.add_conditional_edges(
        "validate_email_draft",
        validation_router,
        {
            "row_review_interrupt_if_needed": "row_review_interrupt_if_needed",
            "send_email_via_gmail": "send_email_via_gmail",
        }
    )
    
    # Review interrupt - conditional
    def review_router(state: RecipientGraphState):
        if state.approval_status == "approved":
            return "send_email_via_gmail"
        elif state.approval_status == "rejected":
            return "persist_send_outcome"  # Skip send
        else:
            return "row_review_interrupt_if_needed"  # Wait
    
    workflow.add_conditional_edges(
        "row_review_interrupt_if_needed",
        review_router,
        {
            "send_email_via_gmail": "send_email_via_gmail",
            "persist_send_outcome": "persist_send_outcome",
            "row_review_interrupt_if_needed": "row_review_interrupt_if_needed",
        }
    )
    
    workflow.add_edge("send_email_via_gmail", "persist_send_outcome")
    workflow.add_edge("persist_send_outcome", END)
    
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


def get_recipient_thread_id(campaign_id: str, recipient_id: str) -> str:
    """Get deterministic thread ID for recipient."""
    return f"campaign:{campaign_id}:recipient:{recipient_id}"
