"""Actual-app tests: real owner middleware, disposable SQLite and real lifespan.

Only Google's verification boundary is mocked for login. Every HTTP request
passes through app.main.app; no provider or campaign background work is allowed.
"""

import csv
import io
import re
import time
import uuid
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import main
from app.api import auth, campaigns
from app.core import owner_access
from app.core.config import settings
from app.db.base import Base, engine
from app.db.models import Campaign, CampaignRow, CampaignStatus, RowStatus, SendEvent

CSV_DATA = b"email,first_name,company\nada@example.invalid,Ada,Synthetic Company\n"


async def clear_disposable_database():
    async with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(table.delete())


@pytest.fixture
def application(tmp_path, monkeypatch):
    """Use the actual bound engine, never an unrelated dependency-overridden DB."""
    database = Path(engine.url.database).resolve()
    assert database.parent.name.startswith("outreach-offline-tests-")
    assert settings.APP_OWNER_EMAIL == "owner@example.invalid"
    assert not main.app.dependency_overrides
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "EMAIL_SENDING_ENABLED", False)
    monkeypatch.setattr(settings, "GMAIL_CONNECTION_ENABLED", False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Integration tests must not start provider or campaign work")

    monkeypatch.setattr(campaigns, "create_campaign_graph", forbidden)
    monkeypatch.setattr(campaigns, "create_recipient_graph", forbidden)
    monkeypatch.setattr(auth, "get_authorization_url", forbidden)
    monkeypatch.setattr(auth, "exchange_code_for_credentials", forbidden)
    origin = main.app.state.owner_access.origin
    with TestClient(main.app, base_url=origin, follow_redirects=False) as client:
        client.portal.call(clear_disposable_database)
    try:
        yield main.app
    finally:
        with TestClient(main.app, base_url=origin, follow_redirects=False) as client:
            client.portal.call(clear_disposable_database)


@pytest.fixture
def client(application):
    with TestClient(
        application, base_url=application.state.owner_access.origin, follow_redirects=False
    ) as client:
        yield client


def login(client, monkeypatch, *, expected=200, **claim_overrides):
    policy = client.app.state.owner_access
    page = client.get("/login", params={"next": "/campaigns"})
    assert page.status_code == 200
    match = re.search(r'data-nonce="([A-Za-z0-9_-]{43})"', page.text)
    assert match, "Login page must supply the nonce bound to its signed challenge cookie"
    assert client.cookies.get(policy.challenge_name)
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": policy.client_id,
        "sub": "synthetic-integration-owner",
        "email": policy.owner_email,
        "email_verified": True,
        "iat": now - 5,
        "exp": now + 600,
        "nonce": match[1],
    }
    claims.update(claim_overrides)
    verifier = Mock(return_value=claims)
    monkeypatch.setattr(owner_access, "_verify_google", verifier)
    response = client.post(
        "/login/google",
        headers={"Origin": policy.origin},
        json={
            "credential": "synthetic-integration-google-credential",
            "next": "/campaigns",
        },
    )
    assert response.status_code == expected, response.text
    verifier.assert_called_once_with("synthetic-integration-google-credential", policy.client_id)
    if expected == 200:
        assert client.cookies.get(policy.cookie_name)
        assert client.cookies.get(policy.challenge_name) is None
        assert response.json() == {"redirect": "/campaigns"}
    return response


def create_campaign(client, *, name="Synthetic private campaign"):
    response = client.post(
        "/api/campaigns",
        headers={"Origin": client.app.state.owner_access.origin},
        json={
            "name": name,
            "context": "Offline integration fixture",
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text
    campaign = response.json()
    assert campaign["dry_run"] is True
    assert campaign["status"] == "created"
    uuid.UUID(campaign["id"])
    return campaign


async def campaign_record(campaign_id):
    async with main.AsyncSessionLocal() as session:
        campaign = await session.get(Campaign, campaign_id)
        return {
            "source": campaign.csv_storage_path,
            "filename": campaign.csv_filename,
            "dry_run": campaign.dry_run,
            "status": campaign.status.value,
        }


async def seed_reviewed_row(campaign_id):
    """Represent completed offline review without invoking a provider graph."""
    async with main.AsyncSessionLocal() as session:
        campaign = await session.get(Campaign, campaign_id)
        campaign.status = CampaignStatus.PAUSED
        campaign.inferred_schema_json = {"primary_email_column": "email"}
        row = CampaignRow(
            campaign_id=campaign_id,
            row_number=1,
            recipient_email="ada@example.invalid",
            status=RowStatus.QUEUED,
            raw_row_json={
                "email": "ada@example.invalid",
                "first_name": "Ada",
                "company": "Synthetic Company",
            },
        )
        session.add(row)
        await session.commit()
        return row.id


async def legacy_live_campaign(campaign_id):
    async with main.AsyncSessionLocal() as session:
        campaign = await session.get(Campaign, campaign_id)
        campaign.dry_run = False
        await session.commit()


async def stored_counts():
    async with main.AsyncSessionLocal() as session:
        return {
            "campaigns": await session.scalar(select(func.count()).select_from(Campaign)),
            "rows": await session.scalar(select(func.count()).select_from(CampaignRow)),
            "sends": await session.scalar(select(func.count()).select_from(SendEvent)),
        }


def test_anonymous_pages_apis_docs_and_gmail_are_denied(client):
    for path in (
        "/",
        "/campaigns",
        "/campaigns/new",
        "/leads",
        "/auth",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
    ):
        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login?")
        assert "no-store" in response.headers["cache-control"]
    for path in (
        "/api/campaigns",
        "/api/leads",
        "/api/followups/stats",
        "/openapi.json",
        "/auth/status",
        "/auth/google/start",
        "/auth/google/callback?code=fake&state=fake",
    ):
        assert client.get(path).status_code == 401, path
    for method, path, payload in (
        ("POST", "/api/campaigns", {"name": "Must not exist", "dry_run": True}),
        ("POST", "/api/followups/send", {"draft_id": "fake", "dry_run": False}),
        ("POST", "/auth/google/disconnect", {}),
        ("PUT", "/auth/signature", {"signature": "Must not persist"}),
    ):
        assert client.request(method, path, json=payload).status_code == 401
    assert client.portal.call(stored_counts) == {"campaigns": 0, "rows": 0, "sends": 0}


def test_probes_are_public_and_reveal_no_private_counts(client, monkeypatch):
    login(client, monkeypatch)
    create_campaign(client, name="Private name must never be in a probe")
    client.cookies.clear()
    assert client.get("/health").json() == {"status": "healthy"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert "no-store" in ready.headers["cache-control"]
    assert client.get("/api/campaigns").status_code == 401


def test_readiness_database_failure_is_a_generic_503(client, monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic-private-database-path-and-counts")

    with monkeypatch.context() as scoped:
        scoped.setattr(main, "AsyncSessionLocal", unavailable)
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "synthetic-private" not in response.text
    assert client.get("/health").status_code == 200


def test_owner_login_pages_session_and_logout_use_real_middleware(client, monkeypatch):
    login(client, monkeypatch)
    session = client.get("/session")
    assert session.json()["authenticated"] is True
    assert session.json()["email"] == "owner@example.invalid"
    assert session.json()["expires_at"] > int(time.time())
    for path in ("/", "/auth", "/campaigns", "/campaigns/new", "/leads", "/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert "text/html" in response.headers["content-type"]
        assert "no-store" in response.headers["cache-control"]
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/auth/status").json() == {"connected": False, "account": None}
    response = client.post("/logout", headers={"Origin": client.app.state.owner_access.origin})
    assert response.status_code == 200
    assert client.get("/session").status_code == 401
    assert client.get("/api/campaigns").status_code == 401
    assert client.get("/campaigns").status_code == 303


@pytest.mark.parametrize(
    "claims,status",
    [
        ({"email": "not-the-owner@example.invalid"}, 403),
        ({"email_verified": False}, 401),
        ({"nonce": "x" * 43}, 401),
    ],
)
def test_real_login_rejects_wrong_account_unverified_email_or_browser_nonce(
    client, monkeypatch, claims, status
):
    login(client, monkeypatch, expected=status, **claims)
    assert client.get("/api/campaigns").status_code == 401


def test_session_csrf_blocks_login_mutation_and_logout_without_state_changes(client, monkeypatch):
    client.get("/login")
    verifier = Mock(side_effect=AssertionError("Cross-origin login must not verify Google"))
    monkeypatch.setattr(owner_access, "_verify_google", verifier)
    for headers in (
        {},
        {"Origin": "https://evil.example.invalid", "X-Forwarded-Host": "evil.example.invalid"},
    ):
        assert (
            client.post("/login/google", headers=headers, json={"credential": "fake"}).status_code
            == 403
        )
    verifier.assert_not_called()
    login(client, monkeypatch)
    for headers in ({}, {"Origin": "https://evil.example.invalid"}):
        assert (
            client.post(
                "/api/campaigns", headers=headers, json={"name": "CSRF", "dry_run": True}
            ).status_code
            == 403
        )
        assert client.post("/logout", headers=headers).status_code == 403
    assert client.get("/session").json()["authenticated"] is True
    assert client.get("/api/campaigns").json()["total"] == 0


def test_dry_campaign_create_list_read_update_delete(client, monkeypatch):
    login(client, monkeypatch)
    campaign_id = create_campaign(client)["id"]
    root = f"/api/campaigns/{campaign_id}"
    listing = client.get("/api/campaigns").json()
    assert listing["total"] == 1 and listing["campaigns"][0]["id"] == campaign_id
    assert client.get(root).json()["context"] == "Offline integration fixture"
    client.portal.call(seed_reviewed_row, campaign_id)
    response = client.put(
        root + "/plan",
        headers={"Origin": client.app.state.owner_access.origin},
        json={
            "tone": "polite",
            "cta": "Reply when convenient",
            "context": "Updated offline context",
        },
    )
    assert response.status_code == 200, response.text
    stored = client.get(root).json()
    assert stored["campaign_plan_json"]["tone"] == "polite"
    assert stored["campaign_plan_json"]["context"] == "Updated offline context"
    rows = client.get(root + "/rows").json()
    assert rows["total"] == 1 and rows["rows"][0]["recipient_email"] == "ada@example.invalid"
    deleted = client.delete(root, headers={"Origin": client.app.state.owner_access.origin})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["new_status"] == "deleted"
    assert client.get(root).status_code == 404
    assert client.get("/api/campaigns").json()["total"] == 0
    assert client.portal.call(stored_counts) == {"campaigns": 0, "rows": 0, "sends": 0}


def test_upload_source_rows_and_private_csv_json_export(client, monkeypatch):
    login(client, monkeypatch)
    campaign_id = create_campaign(client)["id"]
    root = f"/api/campaigns/{campaign_id}"
    upload = client.post(
        root + "/upload",
        headers={"Origin": client.app.state.owner_access.origin},
        files={
            "file": ("synthetic-contacts.csv", CSV_DATA, "text/csv"),
        },
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["row_count"] == 1
    assert upload.json()["columns"] == ["email", "first_name", "company"]
    source = Path(client.portal.call(campaign_record, campaign_id)["source"])
    assert source.parent == Path(settings.UPLOAD_DIR)
    assert source.read_bytes() == CSV_DATA
    assert source.name != "synthetic-contacts.csv"
    assert source.stat().st_mode & 0o777 == 0o600
    assert client.get(root).json()["csv_filename"] == "synthetic-contacts.csv"
    assert client.get(root + "/rows").json()["total"] == 0
    client.portal.call(seed_reviewed_row, campaign_id)
    downloads = []
    for format in ("csv", "json"):
        exported = client.get(root + "/export", params={"format": format})
        assert exported.status_code == 200, exported.text
        payload = exported.json()
        assert payload["row_count"] == 1
        download_url = payload["download_url"]
        assert urlsplit(download_url).path == root + "/export/download"
        assert parse_qs(urlsplit(download_url).query)["file"]
        downloaded = client.get(download_url)
        assert downloaded.status_code == 200, downloaded.text
        assert "attachment" in downloaded.headers["content-disposition"]
        assert "no-store" in downloaded.headers["cache-control"]
        if format == "csv":
            rows = list(csv.DictReader(io.StringIO(downloaded.text)))
            assert rows[0]["recipient_email"] == "ada@example.invalid"
            assert rows[0]["company"] == "Synthetic Company"
        else:
            assert downloaded.json()[0]["raw_data"]["first_name"] == "Ada"
        downloads.append(download_url)
    client.cookies.clear()
    for download_url in downloads:
        assert client.get(download_url).status_code == 401
    assert client.get(root + "/export").status_code == 401
    assert (
        client.post(
            root + "/upload", files={"file": ("blocked.csv", CSV_DATA, "text/csv")}
        ).status_code
        == 401
    )


def test_disabled_live_flags_and_direct_followups_return_503(client, monkeypatch):
    login(client, monkeypatch)
    origin = {"Origin": client.app.state.owner_access.origin}
    denied = client.post(
        "/api/campaigns", headers=origin, json={"name": "Forbidden live", "dry_run": False}
    )
    assert denied.status_code == 503, denied.text
    assert client.get("/api/campaigns").json()["total"] == 0
    campaign_id = create_campaign(client)["id"]
    root = f"/api/campaigns/{campaign_id}"
    assert client.post(root + "/toggle-dry-run", headers=origin).status_code == 503
    assert client.get(root).json()["dry_run"] is True
    client.portal.call(legacy_live_campaign, campaign_id)
    assert client.post(root + "/run", headers=origin).status_code == 503
    assert client.get(root).json()["status"] == "created"
    for path, payload in (
        ("/api/followups/send", {"draft_id": "nonexistent-synthetic-draft", "dry_run": False}),
        (
            "/api/followups/bulk-send",
            {"draft_ids": ["nonexistent-synthetic-draft"], "dry_run": False},
        ),
    ):
        response = client.post(path, headers=origin, json=payload)
        assert response.status_code == 503, response.text
    assert client.get("/auth/google/start").status_code == 503
    assert client.get("/auth/google/callback?code=synthetic&state=synthetic").status_code == 503
    assert client.portal.call(stored_counts) == {"campaigns": 1, "rows": 0, "sends": 0}


def test_upload_waiting_for_manual_analysis_survives_restart_without_error(application, monkeypatch):
    origin = application.state.owner_access.origin
    with TestClient(application, base_url=origin, follow_redirects=False) as first:
        login(first, monkeypatch)
        campaign_id = create_campaign(first)["id"]
        root = f"/api/campaigns/{campaign_id}"
        uploaded = first.post(
            root + "/upload", headers={"Origin": origin},
            files={"file": ("waiting.csv", CSV_DATA, "text/csv")},
        )
        assert uploaded.status_code == 200
        assert first.get(root).json()["status"] == "created"
        source = Path(first.portal.call(campaign_record, campaign_id)["source"])
    with TestClient(application, base_url=origin, follow_redirects=False) as second:
        login(second, monkeypatch)
        saved = second.get(root).json()
        assert saved["status"] == "created" and saved["errors"] == []
        assert second.get(root + "/rows").json()["total"] == 0
        assert source.read_bytes() == CSV_DATA


def test_stored_campaign_source_and_rows_survive_fresh_lifespan(application, monkeypatch):
    origin = application.state.owner_access.origin
    with TestClient(application, base_url=origin, follow_redirects=False) as first:
        login(first, monkeypatch)
        campaign_id = create_campaign(first, name="Synthetic restart persistence")["id"]
        root = f"/api/campaigns/{campaign_id}"
        response = first.post(
            root + "/upload",
            headers={"Origin": origin},
            files={
                "file": ("restart.csv", CSV_DATA, "text/csv"),
            },
        )
        assert response.status_code == 200, response.text
        saved = first.get(root).json()
        assert saved["status"] == "created" and saved["errors"] == []
        row_id = first.portal.call(seed_reviewed_row, campaign_id)
        source = Path(first.portal.call(campaign_record, campaign_id)["source"])
    # This executes main.lifespan's engine.dispose(), then reopens durable SQLite.
    assert source.read_bytes() == CSV_DATA
    assert Path(engine.url.database).is_file()
    with TestClient(application, base_url=origin, follow_redirects=False) as second:
        assert second.get(root).status_code == 401
        login(second, monkeypatch)
        assert second.get(root).json()["name"] == "Synthetic restart persistence"
        assert second.get(root).json()["dry_run"] is True
        assert second.get(root).json()["errors"] == []
        rows = second.get(root + "/rows").json()
        assert rows["total"] == 1 and rows["rows"][0]["id"] == row_id
        assert second.portal.call(campaign_record, campaign_id)["source"] == str(source)
        assert source.read_bytes() == CSV_DATA
        assert second.get("/ready").json() == {"status": "ready"}
