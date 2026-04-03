"""Additional route handlers for templates."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request):
    """Auth page."""
    return templates.TemplateResponse(request, "auth.html", {})


@router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request):
    """Campaigns list page."""
    return templates.TemplateResponse(request, "campaigns.html", {})


@router.get("/campaigns/new", response_class=HTMLResponse)
async def new_campaign_page(request: Request):
    """New campaign page."""
    return templates.TemplateResponse(request, "campaign_new.html", {})


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail_page(request: Request, campaign_id: str):
    """Campaign detail page."""
    return templates.TemplateResponse(request, "campaign_detail.html", {"campaign_id": campaign_id})
