"""Progress tracking for real-time updates."""

import asyncio
import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class AnalysisProgressManager:
    """Manages real-time progress updates for campaign analysis."""
    
    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._progress: dict[str, dict] = {}
    
    def register(self, campaign_id: str) -> asyncio.Queue:
        """Register a campaign for progress tracking."""
        queue = asyncio.Queue()
        self._queues[campaign_id] = queue
        self._progress[campaign_id] = {
            "status": "starting",
            "message": "Initializing analysis...",
            "total_rows": 0,
            "processed_rows": 0,
            "current_row": None,
            "percent_complete": 0,
            "stage": "init",
        }
        return queue
    
    def unregister(self, campaign_id: str):
        """Unregister a campaign from progress tracking."""
        if campaign_id in self._queues:
            del self._queues[campaign_id]
        if campaign_id in self._progress:
            del self._progress[campaign_id]
    
    async def update(self, campaign_id: str, **kwargs):
        """Update progress for a campaign."""
        if campaign_id in self._progress:
            self._progress[campaign_id].update(kwargs)
            
            # Calculate percentage if we have both values
            progress = self._progress[campaign_id]
            if progress.get("total_rows", 0) > 0:
                progress["percent_complete"] = int(
                    (progress.get("processed_rows", 0) / progress["total_rows"]) * 100
                )
            
            # Send to queue if exists
            if campaign_id in self._queues:
                try:
                    await self._queues[campaign_id].put(json.dumps(progress))
                except Exception as e:
                    logger.error(f"Failed to send progress update: {e}")
    
    def get_progress(self, campaign_id: str) -> dict:
        """Get current progress for a campaign."""
        return self._progress.get(campaign_id, {})
    
    async def get_event(self, campaign_id: str) -> str | None:
        """Get next event for a campaign (blocking)."""
        if campaign_id not in self._queues:
            return None
        try:
            return await self._queues[campaign_id].get()
        except Exception:
            return None


# Global instance
progress_manager = AnalysisProgressManager()
