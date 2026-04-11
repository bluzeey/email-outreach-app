"""Run Alembic migrations."""

import sys
sys.path.insert(0, '/Users/sahilmaheshwari/Documents/email-outreach-app')

from alembic.config import Config
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime import migration

# Load config
alembic_cfg = Config('/Users/sahilmaheshwari/Documents/email-outreach-app/alembic.ini')

# Get script directory
script = ScriptDirectory.from_config(alembic_cfg)

# Get current and head revisions
from sqlalchemy import create_engine
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)

with engine.connect() as conn:
    from alembic.runtime.migration import MigrationContext
    context = MigrationContext.configure(conn)
    current_rev = context.get_current_revision()
    head_rev = script.get_current_head()
    
    print(f"Current DB revision: {current_rev}")
    print(f"Head revision: {head_rev}")
    
    if current_rev != head_rev:
        print(f"\nUpgrading from {current_rev} to {head_rev}...")
        try:
            command.upgrade(alembic_cfg, 'head')
            print("✓ Migration successful!")
        except Exception as e:
            print(f"✗ Migration failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n✓ Database is already up to date")
