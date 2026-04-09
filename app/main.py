"""Email Outreach Application - Main Entry Point"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.api import auth, campaigns, followups, leads, pages, reviews
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.base import AsyncSessionLocal, init_db
from app.db.models import Campaign, CampaignStatus

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def recover_interrupted_campaigns(max_retries=3, retry_delay=1.0):
    """Recover campaigns that were interrupted during analysis.
    
    Campaigns stuck in PROFILING state are likely interrupted.
    We'll keep their partial progress and mark them as resumable.
    """
    for attempt in range(max_retries):
        async with AsyncSessionLocal() as session:
            try:
                # Find campaigns stuck in PROFILING state
                result = await session.execute(
                    select(Campaign).where(Campaign.status == CampaignStatus.PROFILING)
                )
                interrupted = result.scalars().all()
                
                for campaign in interrupted:
                    logger.warning(
                        f"Recovering interrupted campaign {campaign.id} from PROFILING state",
                        campaign_id=campaign.id,
                        name=campaign.name,
                    )
                    
                    # Keep status as PROFILING but mark with error message
                    if not campaign.errors:
                        campaign.errors = []
                    
                    # Add recovery message
                    recovery_msg = "Analysis was interrupted. You can resume by clicking 'Resume Analysis'."
                    if recovery_msg not in campaign.errors:
                        campaign.errors.append(recovery_msg)
                    
                    logger.info(
                        f"Campaign {campaign.id} marked for resume",
                        campaign_id=campaign.id,
                    )
                    
                    # Commit immediately for each campaign to avoid holding locks
                    await session.flush()
                
                await session.commit()
                
                if interrupted:
                    logger.info(f"Recovered {len(interrupted)} interrupted campaigns")
                
                return  # Success - exit the function
                
            except OperationalError as e:
                await session.rollback()
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(
                        f"Database locked during campaign recovery, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(f"Failed to recover interrupted campaigns: {e}")
            except Exception as e:
                logger.error(f"Failed to recover interrupted campaigns: {e}")
                await session.rollback()
                return  # Don't retry on non-lock errors


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    await init_db()
    
    # Ensure upload directory exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    
    # Recover interrupted campaigns
    await recover_interrupted_campaigns()
    
    yield
    
    # Shutdown
    pass


app = FastAPI(
    title="Email Outreach App",
    description="Internal single-user email outreach with FastAPI + LangGraph",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page."""
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# Include routers - Pages first for HTML routes to take precedence
app.include_router(pages.router, tags=["pages"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["campaigns"])
app.include_router(reviews.router, prefix="/api/campaigns", tags=["reviews"])
app.include_router(leads.router, prefix="/api/leads", tags=["leads"])
app.include_router(followups.router, prefix="/api/followups", tags=["followups"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
