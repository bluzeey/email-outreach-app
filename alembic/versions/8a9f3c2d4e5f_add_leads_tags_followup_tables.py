"""Add leads, tags, followup tables

Revision ID: 8a9f3c2d4e5f
Revises: 758202c94898
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '8a9f3c2d4e5f'
down_revision = '758202c94898'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create lead_tags table
    op.create_table(
        'lead_tags',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('color', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    
    # Create leads table
    op.create_table(
        'leads',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.Column('last_name', sa.String(), nullable=True),
        sa.Column('company', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('profile_data_json', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'RESPONDED', 'DO_NOT_CONTACT', 'BOUNCED', name='leadstatus'), nullable=True),
        sa.Column('has_received_followup', sa.Boolean(), nullable=True),
        sa.Column('followup_sent_at', sa.DateTime(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_leads_email'), 'leads', ['email'], unique=False)
    
    # Create lead_tag_associations table
    op.create_table(
        'lead_tag_associations',
        sa.Column('lead_id', sa.String(), nullable=False),
        sa.Column('tag_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
        sa.ForeignKeyConstraint(['tag_id'], ['lead_tags.id'], ),
        sa.PrimaryKeyConstraint('lead_id', 'tag_id')
    )
    
    # Add lead_id to campaign_rows
    op.add_column('campaign_rows', sa.Column('lead_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_campaign_rows_lead_id', 'campaign_rows', 'leads', ['lead_id'], ['id'])
    
    # Add thread_id and followup fields to send_events
    op.add_column('send_events', sa.Column('provider_thread_id', sa.String(), nullable=True))
    op.add_column('send_events', sa.Column('is_followup', sa.Boolean(), nullable=True))
    op.add_column('send_events', sa.Column('original_send_event_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_send_events_original', 'send_events', 'send_events', ['original_send_event_id'], ['id'])
    
    # Create followup_drafts table
    op.create_table(
        'followup_drafts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('campaign_row_id', sa.String(), nullable=False),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('plain_text_body', sa.Text(), nullable=False),
        sa.Column('html_body', sa.Text(), nullable=False),
        sa.Column('original_send_event_id', sa.String(), nullable=True),
        sa.Column('context_summary', sa.Text(), nullable=True),
        sa.Column('generation_confidence', sa.Integer(), nullable=True),
        sa.Column('needs_human_review', sa.Boolean(), nullable=True),
        sa.Column('review_reasons', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['campaign_row_id'], ['campaign_rows.id'], ),
        sa.ForeignKeyConstraint(['original_send_event_id'], ['send_events.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    # Drop followup_drafts
    op.drop_table('followup_drafts')
    
    # Drop send_events followup columns
    op.drop_constraint('fk_send_events_original', 'send_events', type_='foreignkey')
    op.drop_column('send_events', 'original_send_event_id')
    op.drop_column('send_events', 'is_followup')
    op.drop_column('send_events', 'provider_thread_id')
    
    # Drop campaign_rows lead_id
    op.drop_constraint('fk_campaign_rows_lead_id', 'campaign_rows', type_='foreignkey')
    op.drop_column('campaign_rows', 'lead_id')
    
    # Drop lead tables
    op.drop_table('lead_tag_associations')
    op.drop_index(op.f('ix_leads_email'), table_name='leads')
    op.drop_table('leads')
    op.drop_table('lead_tags')
