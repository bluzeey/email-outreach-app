"""Verify and fix database schema."""

import sys
sys.path.insert(0, '/Users/sahilmaheshwari/Documents/email-outreach-app')

from sqlalchemy import create_engine, text, inspect
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)

with engine.connect() as conn:
    # Check campaign_rows columns
    result = conn.execute(text("PRAGMA table_info(campaign_rows)"))
    columns = {row[1] for row in result}
    print(f"campaign_rows columns: {sorted(columns)}")
    
    if 'lead_id' not in columns:
        print("\n⚠️ Missing lead_id column - adding it...")
        conn.execute(text("ALTER TABLE campaign_rows ADD COLUMN lead_id VARCHAR"))
        conn.commit()
        print("✓ Added lead_id column")
    
    # Check send_events columns
    result = conn.execute(text("PRAGMA table_info(send_events)"))
    columns = {row[1] for row in result}
    print(f"\nsend_events columns: {sorted(columns)}")
    
    missing_send_columns = []
    if 'provider_thread_id' not in columns:
        missing_send_columns.append(('provider_thread_id', 'VARCHAR'))
    if 'is_followup' not in columns:
        missing_send_columns.append(('is_followup', 'BOOLEAN'))
    if 'original_send_event_id' not in columns:
        missing_send_columns.append(('original_send_event_id', 'VARCHAR'))
    
    for col_name, col_type in missing_send_columns:
        print(f"\n⚠️ Missing {col_name} column - adding it...")
        conn.execute(text(f"ALTER TABLE send_events ADD COLUMN {col_name} {col_type}"))
        conn.commit()
        print(f"✓ Added {col_name} column")
    
    if not missing_send_columns:
        print("\n✓ All send_events columns present")

print("\n✓ Database schema verified and fixed!")
