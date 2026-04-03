"""Integration tests for campaign workflow."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.main import app

# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    """Create a fresh database session for tests."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as session:
        yield session
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestCampaignWorkflow:
    """Integration tests for full campaign workflow."""
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
    
    def test_create_campaign(self, client):
        """Test creating a campaign."""
        response = client.post(
            "/campaigns",
            json={"name": "Test Campaign", "dry_run": True}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Campaign"
        assert data["dry_run"] is True
        assert "id" in data
    
    def test_list_campaigns(self, client):
        """Test listing campaigns."""
        # First create a campaign
        client.post("/campaigns", json={"name": "Test Campaign", "dry_run": True})
        
        response = client.get("/campaigns")
        
        assert response.status_code == 200
        data = response.json()
        assert "campaigns" in data
        assert data["total"] >= 1
    
    def test_auth_status_not_connected(self, client):
        """Test auth status when not connected."""
        response = client.get("/auth/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False
