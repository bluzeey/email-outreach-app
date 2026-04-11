"""Backfill lead records for existing campaign rows."""

import sys
import asyncio
sys.path.insert(0, '/Users/sahilmaheshwari/Documents/email-outreach-app')

from sqlalchemy import create_engine, text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.core.config import settings
from app.db.base import AsyncSessionLocal, init_db
from app.db.models import CampaignRow, Lead, LeadStatus
from app.services.lead_service import LeadService

async def backfill_leads():
    """Create lead records for existing campaign rows."""
    print("Starting lead backfill...")
    
    async with AsyncSessionLocal() as session:
        lead_service = LeadService(session)
        
        # Get all campaign rows without lead_id
        result = await session.execute(
            select(CampaignRow).where(CampaignRow.lead_id.is_(None))
        )
        rows = result.scalars().all()
        
        print(f"Found {len(rows)} campaign rows without lead_id")
        
        created = 0
        updated = 0
        
        for row in rows:
            if not row.recipient_email:
                continue
            
            # Get or create lead
            lead = await lead_service.get_or_create_lead(row.recipient_email)
            
            # Link the row to the lead
            row.lead_id = lead.id
            updated += 1
            
            if updated % 50 == 0:
                await session.commit()
                print(f"  Processed {updated} rows...")
        
        await session.commit()
        
        print(f"\n✓ Backfill complete!")
        print(f"  Updated {updated} campaign rows")
        
        # Count final leads
        result = await session.execute(select(Lead))
        leads = result.scalars().all()
        print(f"  Total leads in database: {len(leads)}")

if __name__ == "__main__":
    asyncio.run(backfill_leads())
