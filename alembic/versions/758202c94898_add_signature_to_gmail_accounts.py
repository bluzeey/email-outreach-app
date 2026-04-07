"""add_signature_to_gmail_accounts

Revision ID: 758202c94898
Revises: 2bb3d6b44b70
Create Date: 2026-04-07 18:50:50.692957

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '758202c94898'
down_revision = '2bb3d6b44b70'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add signature column to gmail_accounts table
    op.add_column('gmail_accounts', sa.Column('signature', sa.Text(), nullable=True))


def downgrade() -> None:
    # Remove signature column from gmail_accounts table
    op.drop_column('gmail_accounts', 'signature')
