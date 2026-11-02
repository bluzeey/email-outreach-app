"""Sending locks cannot be bypassed by campaign flags or cached Gmail clients."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.config import Settings, settings
from app.core.provider_guards import EmailSendingDisabled, GmailAccessDisabled
from app.services import gmail_client as gmail
from app.services.send_service import SendService


def draft_and_row():
    return (SimpleNamespace(subject="Synthetic subject", plain_text_body="Synthetic body", html_body="<p>Synthetic</p>"),
            SimpleNamespace(campaign_id="synthetic-campaign", id="synthetic-row", recipient_email="recipient@example.invalid"))


def test_new_installation_provider_locks_default_to_disabled(monkeypatch):
    monkeypatch.delenv("EMAIL_SENDING_ENABLED", raising=False)
    monkeypatch.delenv("GMAIL_CONNECTION_ENABLED", raising=False)
    value = Settings(_env_file=None)
    assert value.EMAIL_SENDING_ENABLED is False
    assert value.GMAIL_CONNECTION_ENABLED is False


@pytest.mark.asyncio
async def test_explicit_live_flag_cannot_override_server_lock(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_SENDING_ENABLED", False)
    client = MagicMock()
    draft, row = draft_and_row()
    with pytest.raises(EmailSendingDisabled):
        await SendService().send_email(client, "owner@example.invalid", row, draft, dry_run=False)
    client.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_still_produces_a_preview_without_gmail(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_SENDING_ENABLED", False)
    client = MagicMock()
    draft, row = draft_and_row()
    result = await SendService().send_email(client, "owner@example.invalid", row, draft, dry_run=True)
    assert result["success"] and result["dry_run"]
    assert "message_id" not in result
    client.send_email.assert_not_called()


def test_low_level_send_blocks_before_cached_service_or_token_refresh(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_SENDING_ENABLED", False)
    client = gmail.GmailClient.__new__(gmail.GmailClient)
    client._get_service = MagicMock(side_effect=AssertionError("Must not access Gmail"))
    with pytest.raises(EmailSendingDisabled):
        client.send_email("owner@example.invalid", "recipient@example.invalid", "Synthetic", "Body", "<p>Body</p>")
    client._get_service.assert_not_called()


def test_cached_gmail_service_is_inaccessible_when_connection_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GMAIL_CONNECTION_ENABLED", False)
    client = gmail.GmailClient.__new__(gmail.GmailClient)
    client.service = MagicMock()
    with pytest.raises(GmailAccessDisabled):
        client.get_profile()
    client.service.users.assert_not_called()


def test_oauth_and_refresh_cannot_contact_google_when_gmail_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GMAIL_CONNECTION_ENABLED", False)
    flow = MagicMock()
    monkeypatch.setattr(gmail.Flow, "from_client_config", flow)
    credentials = MagicMock(expired=True, refresh_token="synthetic")
    for operation in (gmail.create_auth_flow, lambda: gmail.exchange_code_for_credentials("synthetic", "synthetic"),
                      lambda: gmail.refresh_credentials_if_needed(credentials), lambda: gmail.build_gmail_service(credentials)):
        with pytest.raises(GmailAccessDisabled):
            operation()
    flow.assert_not_called()
    credentials.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_live_campaign_creation_rejected_before_any_database_write(monkeypatch):
    from app.api.campaigns import create_campaign
    from app.schemas.campaign import CampaignCreateRequest
    monkeypatch.setattr(settings, "EMAIL_SENDING_ENABLED", False)
    db = MagicMock()
    with pytest.raises(HTTPException) as error:
        await create_campaign(CampaignCreateRequest(name="Synthetic", dry_run=False), db)
    assert error.value.status_code == 503
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_direct_and_bulk_followup_send_rejected_before_database_access(monkeypatch):
    from app.api.followups import bulk_send_followups, send_followup
    from app.schemas.followup import FollowupBulkSendRequest, FollowupSendRequest
    monkeypatch.setattr(settings, "EMAIL_SENDING_ENABLED", False)
    db = MagicMock()
    requests = [(send_followup, FollowupSendRequest(draft_id="synthetic", dry_run=False)),
                (bulk_send_followups, FollowupBulkSendRequest(draft_ids=["synthetic"], dry_run=False))]
    for handler, request in requests:
        with pytest.raises(HTTPException) as error:
            await handler(request, db)
        assert error.value.status_code == 503
    db.get.assert_not_called()
