"""Authentication API endpoints."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import encrypt_token
from app.db.base import AsyncSessionLocal
from app.db.models import GmailAccount
from app.schemas.auth import (
    AuthCallbackRequest,
    AuthStatusResponse,
    AuthUrlResponse,
    GmailAccountResponse,
)
from app.services.gmail_client import (
    get_authorization_url,
    exchange_code_for_credentials,
    dict_to_credentials,
    build_gmail_service,
)

logger = get_logger(__name__)
router = APIRouter()


async def get_session():
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@router.get("/google/start")
async def start_google_auth():
    """Start Google OAuth flow - redirects to Google."""
    try:
        auth_url, state = get_authorization_url()
        return RedirectResponse(url=auth_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start OAuth: {e}")
        raise HTTPException(status_code=500, detail="Failed to start authentication")


@router.get("/google/callback")
async def google_auth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Handle Google OAuth callback."""
    try:
        # Exchange code for credentials
        credentials = exchange_code_for_credentials(code, state)
        
        # Get user email using OAuth2 userinfo API (requires userinfo.email scope)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {credentials['token']}"}
            )
            response.raise_for_status()
            userinfo = response.json()
            email = userinfo.get("email")
        
        if not email:
            raise ValueError("Could not retrieve email from userinfo")
        
        # Check if account exists
        result = await session.execute(
            select(GmailAccount).where(GmailAccount.email == email)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing
            existing.token_encrypted = encrypt_token(credentials["token"])
            existing.refresh_token_encrypted = encrypt_token(credentials["refresh_token"]) if credentials.get("refresh_token") else None
            existing.status = "active"
            account = existing
        else:
            # Create new
            account = GmailAccount(
                email=email,
                scopes=credentials["scopes"],
                token_encrypted=encrypt_token(credentials["token"]),
                refresh_token_encrypted=encrypt_token(credentials["refresh_token"]) if credentials.get("refresh_token") else None,
                status="active",
            )
            session.add(account)
        
        await session.commit()
        
        logger.info(f"Gmail account connected: {email}")
        
        # Redirect to success page
        return RedirectResponse(url="/?auth=success")
        
    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")


@router.post("/google/disconnect")
async def disconnect_google_auth(
    session: AsyncSession = Depends(get_session),
):
    """Disconnect Gmail account."""
    try:
        # Get connected account
        result = await session.execute(
            select(GmailAccount).where(GmailAccount.status == "active")
        )
        account = result.scalar_one_or_none()
        
        if account:
            account.status = "disconnected"
            await session.commit()
            logger.info(f"Gmail account disconnected: {account.email}")
        
        return {"success": True, "message": "Account disconnected"}
        
    except Exception as e:
        logger.error(f"Disconnect failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect account")


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(
    session: AsyncSession = Depends(get_session),
):
    """Get authentication status."""
    try:
        result = await session.execute(
            select(GmailAccount).where(GmailAccount.status == "active")
        )
        account = result.scalar_one_or_none()
        
        if account:
            return AuthStatusResponse(
                connected=True,
                account=GmailAccountResponse(
                    id=account.id,
                    email=account.email,
                    status=account.status,
                    connected_at=account.connected_at.isoformat() if account.connected_at else "",
                    scopes=account.scopes or [],
                )
            )
        else:
            return AuthStatusResponse(connected=False, account=None)
            
    except Exception as e:
        logger.error(f"Failed to get auth status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get authentication status")
