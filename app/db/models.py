"""Database models."""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class CampaignStatus(str, enum.Enum):
    CREATED = "created"
    PROFILING = "profiling"
    AWAITING_SCHEMA_REVIEW = "awaiting_schema_review"
    AWAITING_CAMPAIGN_APPROVAL = "awaiting_campaign_approval"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RowStatus(str, enum.Enum):
    QUEUED = "queued"
    NORMALIZED = "normalized"
    INELIGIBLE = "ineligible"
    GENERATED = "generated"
    VALIDATED = "validated"
    AWAITING_REVIEW = "awaiting_review"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class SendStatus(str, enum.Enum):
    PENDING = "pending"
    DRY_RUN = "dry_run"
    SENT = "sent"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class ApprovalDecision(str, enum.Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class LeadStatus(str, enum.Enum):
    """Lead lifecycle status."""
    ACTIVE = "active"  # Can receive followups
    RESPONDED = "responded"  # Has responded, no followups
    DO_NOT_CONTACT = "do_not_contact"  # Explicitly opted out/unsubscribed
    BOUNCED = "bounced"  # Email bounced


class LeadTag(Base):
    """Tag for categorizing leads (e.g., 'researchers', 'startups', 'vip')."""
    
    __tablename__ = "lead_tags"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    color = Column(String, nullable=True)  # Hex color for UI
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    leads = relationship("Lead", secondary="lead_tag_associations", back_populates="tags")


class LeadTagAssociation(Base):
    """Many-to-many association between leads and tags."""
    
    __tablename__ = "lead_tag_associations"
    
    lead_id = Column(String, ForeignKey("leads.id"), primary_key=True)
    tag_id = Column(String, ForeignKey("lead_tags.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Lead(Base):
    """Global lead (contact) model - unique by email across all campaigns."""
    
    __tablename__ = "leads"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False, unique=True, index=True)
    
    # Profile info (merged from campaign data)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    company = Column(String, nullable=True)
    title = Column(String, nullable=True)
    profile_data_json = Column(JSON, default=dict)  # Additional merged fields
    
    # Lifecycle status
    status = Column(Enum(LeadStatus), default=LeadStatus.ACTIVE)
    
    # Followup tracking (one followup max)
    has_received_followup = Column(Boolean, default=False)
    followup_sent_at = Column(DateTime, nullable=True)
    
    # Metadata
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    campaign_rows = relationship("CampaignRow", back_populates="lead")
    tags = relationship("LeadTag", secondary="lead_tag_associations", back_populates="leads")


class GmailAccount(Base):
    """Gmail account model."""
    
    __tablename__ = "gmail_accounts"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False, unique=True)
    sender_name = Column(String, nullable=True)  # Full name from Google OAuth
    signature = Column(Text, nullable=True)  # Custom email signature
    provider = Column(String, default="google")
    scopes = Column(JSON, default=list)
    token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    connected_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="active")
    
    # Relationships
    campaigns = relationship("Campaign", back_populates="gmail_account")


class Campaign(Base):
    """Campaign model."""
    
    __tablename__ = "campaigns"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    context = Column(Text, nullable=True)  # Campaign context/purpose for AI
    gmail_account_id = Column(String, ForeignKey("gmail_accounts.id"), nullable=True)
    
    # Status
    status = Column(Enum(CampaignStatus), default=CampaignStatus.CREATED)
    
    # CSV Storage
    csv_filename = Column(String, nullable=True)
    csv_storage_path = Column(String, nullable=True)
    
    # Inferred data
    inferred_schema_json = Column(JSON, default=dict)
    campaign_plan_json = Column(JSON, default=dict)
    sample_drafts_json = Column(JSON, default=list)  # Store AI-generated sample drafts
    
    # Execution tracking
    totals_json = Column(JSON, default=dict)
    dry_run = Column(Boolean, default=True)
    dispatch_cursor = Column(Integer, default=0)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    errors = Column(JSON, default=list)
    
    # Relationships
    gmail_account = relationship("GmailAccount", back_populates="campaigns")
    rows = relationship("CampaignRow", back_populates="campaign", cascade="all, delete-orphan")


class CampaignRow(Base):
    """Campaign row (recipient) model - links to global Lead."""
    
    __tablename__ = "campaign_rows"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=True)  # Link to global lead
    row_number = Column(Integer, nullable=False)
    
    # Raw data
    raw_row_json = Column(JSON, default=dict)
    normalized_row_json = Column(JSON, default=dict)
    
    # Recipient info (also stored on Lead, kept here for convenience)
    recipient_email = Column(String, nullable=True)
    
    # Status
    status = Column(Enum(RowStatus), default=RowStatus.QUEUED)
    
    # Validation
    validation_report_json = Column(JSON, default=dict)
    eligibility_json = Column(JSON, default=dict)
    personalization_context_json = Column(JSON, default=dict)
    
    # Error tracking
    error_message = Column(Text, nullable=True)
    errors = Column(JSON, default=list)
    retries = Column(Integer, default=0)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    campaign = relationship("Campaign", back_populates="rows")
    lead = relationship("Lead", back_populates="campaign_rows")
    email_draft = relationship("EmailDraft", back_populates="campaign_row", uselist=False)
    send_event = relationship("SendEvent", back_populates="campaign_row", uselist=False)
    followup_drafts = relationship("FollowupDraft", back_populates="campaign_row")


class EmailDraft(Base):
    """Email draft model."""
    
    __tablename__ = "email_drafts"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_row_id = Column(String, ForeignKey("campaign_rows.id"), nullable=False)
    
    # Content
    subject = Column(String, nullable=False)
    plain_text_body = Column(Text, nullable=False)
    html_body = Column(Text, nullable=False)
    
    # Generation metadata
    personalization_fields_used = Column(JSON, default=list)
    key_claims_used = Column(JSON, default=list)
    generation_confidence = Column(Integer, default=0)  # 0-100
    needs_human_review = Column(Boolean, default=False)
    review_reasons = Column(JSON, default=list)
    generation_meta_json = Column(JSON, default=dict)
    
    # Validation
    validation_report_json = Column(JSON, default=dict)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    campaign_row = relationship("CampaignRow", back_populates="email_draft")


class SendEvent(Base):
    """Send event model."""
    
    __tablename__ = "send_events"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_row_id = Column(String, ForeignKey("campaign_rows.id"), nullable=False)
    
    # Idempotency
    idempotency_key = Column(String, nullable=False, unique=True)
    
    # Provider info
    provider = Column(String, default="gmail")
    provider_message_id = Column(String, nullable=True)  # Gmail message ID
    provider_thread_id = Column(String, nullable=True)   # Gmail thread ID for followups
    provider_response_json = Column(JSON, default=dict)
    
    # Email type
    is_followup = Column(Boolean, default=False)  # Whether this is a followup email
    original_send_event_id = Column(String, ForeignKey("send_events.id"), nullable=True)  # For followups, link to original
    
    # Status
    status = Column(Enum(SendStatus), default=SendStatus.PENDING)
    
    # Timing
    sent_at = Column(DateTime, nullable=True)
    
    # Error tracking
    error_message = Column(Text, nullable=True)
    
    # Relationships
    campaign_row = relationship("CampaignRow", back_populates="send_event")
    original_send_event = relationship("SendEvent", remote_side=[id], backref="followup_events")


class FollowupDraft(Base):
    """Followup email draft for a campaign row."""
    
    __tablename__ = "followup_drafts"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_row_id = Column(String, ForeignKey("campaign_rows.id"), nullable=False)
    
    # Content
    subject = Column(String, nullable=False)
    plain_text_body = Column(Text, nullable=False)
    html_body = Column(Text, nullable=False)
    
    # Context for generation
    original_send_event_id = Column(String, ForeignKey("send_events.id"), nullable=True)
    context_summary = Column(Text, nullable=True)  # AI summary of original email context
    
    # Generation metadata
    generation_confidence = Column(Integer, default=0)  # 0-100
    needs_human_review = Column(Boolean, default=False)
    review_reasons = Column(JSON, default=list)
    
    # Status
    status = Column(String, default="draft")  # draft, approved, rejected, sent
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    campaign_row = relationship("CampaignRow", back_populates="followup_drafts")
    original_send_event = relationship("SendEvent", foreign_keys=[original_send_event_id])


class ApprovalEvent(Base):
    """Approval event model."""
    
    __tablename__ = "approval_events"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    campaign_row_id = Column(String, ForeignKey("campaign_rows.id"), nullable=True)
    
    # Decision
    decision = Column(Enum(ApprovalDecision), nullable=False)
    
    # Reviewer info
    reviewer = Column(String, default="system")
    notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
