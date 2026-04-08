#!/usr/bin/env python3
"""Database cleanup script to dedupe email_drafts and add unique constraint."""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import AsyncSessionLocal, engine
from app.db.models import EmailDraft, CampaignRow
from app.core.logging import get_logger

logger = get_logger(__name__)


async def dedupe_email_drafts():
    """Remove duplicate email drafts, keeping the newest one per campaign_row_id."""
    async with AsyncSessionLocal() as session:
        try:
            # Find all campaign_row_ids with multiple drafts
            result = await session.execute(
                text("""
                    SELECT campaign_row_id, COUNT(*) as count
                    FROM email_drafts
                    GROUP BY campaign_row_id
                    HAVING COUNT(*) > 1
                """)
            )
            duplicates = result.all()

            if not duplicates:
                logger.info("No duplicate email drafts found!")
                return 0

            total_removed = 0

            for campaign_row_id, count in duplicates:
                logger.info(f"Found {count} drafts for row {campaign_row_id}")

                # Get all drafts for this row, ordered by created_at desc, id desc
                drafts_result = await session.execute(
                    text("""
                        SELECT id, created_at
                        FROM email_drafts
                        WHERE campaign_row_id = :row_id
                        ORDER BY created_at DESC, id DESC
                    """),
                    {"row_id": campaign_row_id}
                )
                drafts = drafts_result.all()

                if len(drafts) > 1:
                    # Keep the first one (newest), delete the rest
                    newest_id = drafts[0][0]
                    ids_to_delete = [d[0] for d in drafts[1:]]

                    logger.info(f"  Keeping newest draft: {newest_id}")
                    logger.info(f"  Deleting {len(ids_to_delete)} older drafts: {ids_to_delete}")

                    # Delete older drafts (SQLite doesn't support ANY, use IN)
                    placeholders = ', '.join([f':id{i}' for i in range(len(ids_to_delete))])
                    delete_sql = f"DELETE FROM email_drafts WHERE id IN ({placeholders})"
                    params = {f'id{i}': ids_to_delete[i] for i in range(len(ids_to_delete))}
                    await session.execute(text(delete_sql), params)
                    total_removed += len(ids_to_delete)

            await session.commit()
            logger.info(f"Cleanup complete! Removed {total_removed} duplicate drafts.")
            return total_removed

        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to dedupe email drafts: {e}")
            raise


async def add_unique_constraint():
    """Add unique index on email_drafts.campaign_row_id."""
    async with engine.begin() as conn:
        try:
            # Check if index already exists
            result = await conn.execute(
                text("""
                    SELECT name FROM sqlite_master
                    WHERE type='index' AND tbl_name='email_drafts'
                    AND name='idx_email_drafts_campaign_row_id_unique'
                """)
            )
            if result.scalar():
                logger.info("Unique index already exists on email_drafts.campaign_row_id")
                return

            # Create unique index
            await conn.execute(
                text("""
                    CREATE UNIQUE INDEX idx_email_drafts_campaign_row_id_unique
                    ON email_drafts(campaign_row_id)
                """)
            )
            logger.info("Created unique index on email_drafts.campaign_row_id")

        except Exception as e:
            logger.error(f"Failed to add unique constraint: {e}")
            raise


async def verify_cleanup():
    """Verify no duplicates remain."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM (
                    SELECT campaign_row_id
                    FROM email_drafts
                    GROUP BY campaign_row_id
                    HAVING COUNT(*) > 1
                )
            """)
        )
        duplicate_count = result.scalar()

        if duplicate_count == 0:
            logger.info("✓ Verification passed: No duplicate drafts remain")
        else:
            logger.warning(f"✗ Verification failed: {duplicate_count} rows still have duplicates")

        # Count total drafts
        total_result = await session.execute(
            text("SELECT COUNT(*) FROM email_drafts")
        )
        total = total_result.scalar()
        logger.info(f"Total email_drafts: {total}")


async def main():
    """Run cleanup and add constraint."""
    logger.info("=" * 60)
    logger.info("Starting email_drafts cleanup and constraint setup")
    logger.info("=" * 60)

    # Step 1: Dedupe
    removed = await dedupe_email_drafts()

    # Step 2: Add unique constraint
    await add_unique_constraint()

    # Step 3: Verify
    await verify_cleanup()

    logger.info("=" * 60)
    logger.info("Cleanup complete!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
