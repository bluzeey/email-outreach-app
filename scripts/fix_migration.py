"""Stamp and run Alembic migrations."""

import sys
sys.path.insert(0, '/Users/sahilmaheshwari/Documents/email-outreach-app')

from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, text
from app.core.config import settings

# Load config
alembic_cfg = Config('/Users/sahilmaheshwari/Documents/email-outreach-app/alembic.ini')

# Check what tables exist
engine = create_engine(settings.DATABASE_URL)

with engine.connect() as conn:
    result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    tables = [row[0] for row in result]
    print(f"Existing tables: {tables}")
    
    # Check if lead tables exist
    has_lead_tables = 'leads' in tables and 'lead_tags' in tables
    print(f"Lead tables exist: {has_lead_tables}")
    
    # Check alembic version
    try:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar()
        print(f"Current alembic version: {version}")
    except:
        print("No alembic version table found")
        version = None

# If lead tables exist but migration hasn't run, stamp the DB
if has_lead_tables:
    print("\nLead tables already exist. Stamping database to previous revision...")
    try:
        # First stamp to the revision before our new one
        command.stamp(alembic_cfg, '758202c94898')
        print("✓ Stamped to 758202c94898")
    except Exception as e:
        print(f"Stamp error (may be already stamped): {e}")

# Now run the migration
print("\nRunning migration...")
try:
    command.upgrade(alembic_cfg, 'head')
    print("✓ Migration successful!")
except Exception as e:
    print(f"✗ Migration error: {e}")
    
    # Check if it's because column already exists
    if 'already exists' in str(e).lower() or 'duplicate column' in str(e).lower():
        print("\nAttempting to stamp to head and skip migration...")
        try:
            command.stamp(alembic_cfg, 'head')
            print("✓ Stamped to head")
        except Exception as e2:
            print(f"Stamp failed: {e2}")

# Verify final state
with engine.connect() as conn:
    try:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        final_version = result.scalar()
        print(f"\nFinal alembic version: {final_version}")
    except:
        print("\nCould not read final version")
