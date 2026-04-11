"""Run Alembic migrations programmatically."""

import asyncio
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.runtime import migration

# Add app to path
sys.path.insert(0, '/Users/sahilmaheshwari/Documents/email-outreach-app')

from app.core.config import settings

# Create Alembic config
alembic_cfg = Config('/Users/sahilmaheshwari/Documents/email-outreach-app/alembic.ini')

# Get database URL
database_url = settings.DATABASE_URL
print(f"Database URL: {database_url}")

# Create engine
engine = engine_from_config(
    {'sqlalchemy.url': database_url},
    prefix='sqlalchemy.',
    poolclass=pool.NullPool,
)

# Get current revision
with engine.connect() as connection:
    context.configure(connection=connection)
    
    # Get current revision
    from alembic.runtime.migration import MigrationContext
    migration_context = MigrationContext.configure(connection)
    current_rev = migration_context.get_current_revision()
    print(f"Current revision: {current_rev}")
    
    # Get script directory
    script = ScriptDirectory.from_config(alembic_cfg)
    head_rev = script.get_current_head()
    print(f"Head revision: {head_rev}")
    
    if current_rev == head_rev:
        print("Database is up to date!")
    else:
        print(f"Need to upgrade from {current_rev} to {head_rev}")
        
        # Run upgrade
        def upgrade():
            with engine.begin() as conn:
                context.configure(connection=conn, target_metadata=None)
                with context.begin_transaction():
                    context.run_migrations()
        
        # Import and run the migration
        from alembic import command
        alembic_cfg.attributes['connection'] = connection
        command.upgrade(alembic_cfg, 'head')
        
        print("Migration complete!")
        
        # Verify
        new_rev = migration_context.get_current_revision()
        print(f"New revision: {new_rev}")
