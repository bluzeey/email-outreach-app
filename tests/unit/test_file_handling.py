"""Offline file handling tests: real ASGI routes, synthetic DB and local files."""

import asyncio
import csv
import io
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import campaigns
from app.core.config import settings
from app.core.file_paths import export_basename, upload_destination
from app.core.owner_access import SESSION_SECONDS, setup_owner_access
from app.db.base import Base
from app.db.models import Campaign, CampaignRow, CampaignStatus, RowStatus
from app.services.csv_loader import DataLoader


@pytest_asyncio.fixture
async def files_app(tmp_path, monkeypatch):
    directory = tmp_path / "uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(directory))
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE", 10 * 1024 * 1024)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fixtures.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        first = Campaign(id=str(uuid.uuid4()), name="Synthetic A", dry_run=True)
        second = Campaign(id=str(uuid.uuid4()), name="Synthetic B", dry_run=True)
        session.add_all([first, second])
        await session.commit()
        app = FastAPI()
        app.include_router(campaigns.router, prefix="/api/campaigns")

        async def get_session():
            yield session

        app.dependency_overrides[campaigns.get_session] = get_session
        access = setup_owner_access(app, settings=SimpleNamespace(
            APP_LOGIN_REQUIRED=True, APP_OWNER_EMAIL="owner@example.invalid",
            APP_GOOGLE_CLIENT_ID="offline-file-tests.apps.googleusercontent.com",
            APP_SESSION_SECRET="offline-only-file-tests-secret-0123456789012345",
            APP_BASE_URL="http://127.0.0.1:8000",
        ))
        now = int(time.time())
        cookie = access.serializer("session").dumps({
            "v": 1, "email": access.owner_email, "sub": "offline-file-owner",
            "iat": now, "exp": now + SESSION_SECONDS,
        })
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://127.0.0.1:8000",
            headers={"Origin": "http://127.0.0.1:8000", "Accept": "application/json"},
        ) as client:
            client.cookies.set(access.cookie_name, cookie)
            yield SimpleNamespace(client=client, session=session, first=first, second=second,
                                  directory=directory, app=app)
    await engine.dispose()


async def generate(app, campaign=None, format="csv"):
    campaign = campaign or app.first
    response = await app.client.get(f"/api/campaigns/{campaign.id}/export", params={"format": format})
    assert response.status_code == 200, response.text
    return response.json()


def export_token(payload):
    return parse_qs(urlsplit(payload["download_url"]).query)["file"][0]


@pytest.mark.asyncio
async def test_csv_export_download_route_and_campaign_binding(files_app):
    app = files_app
    app.session.add(CampaignRow(
        id=str(uuid.uuid4()), campaign_id=app.first.id, row_number=1,
        recipient_email="qa@example.invalid", status=RowStatus.SENT,
        raw_row_json={"company": "Synthetic company", "notes": 'A comma, and "quote"'},
    ))
    await app.session.commit()
    payload = await generate(app)
    assert payload["row_count"] == 1
    response = await app.client.get(payload["download_url"])
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["recipient_email"] == "qa@example.invalid"
    assert rows[0]["notes"] == 'A comma, and "quote"'
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")
    assert "no-store" in response.headers["cache-control"]
    stored = app.directory / export_basename(app.first.id, export_token(payload))
    assert stored.stat().st_mode & 0o777 == 0o600
    wrong = await app.client.get(f"/api/campaigns/{app.second.id}/export/download",
                                 params={"file": export_token(payload)})
    assert wrong.status_code == 404


@pytest.mark.asyncio
async def test_empty_csv_has_stable_headers_and_json_is_valid(files_app):
    app = files_app
    payload = await generate(app)
    response = await app.client.get(payload["download_url"])
    reader = csv.DictReader(io.StringIO(response.text))
    assert reader.fieldnames == ["row_number", "recipient_email", "status"]
    assert list(reader) == []
    payload = await generate(app, format="json")
    response = await app.client.get(payload["download_url"])
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_json_export_preserves_rows_and_errors(files_app):
    app = files_app
    app.session.add(CampaignRow(campaign_id=app.first.id, row_number=4,
        recipient_email="qa@example.invalid", status=RowStatus.FAILED,
        raw_row_json={"company": "Synthetic"}, errors=["synthetic failure"]))
    await app.session.commit()
    response = await app.client.get((await generate(app, format="json"))["download_url"])
    assert response.json() == [{"row_number": 4, "recipient_email": "qa@example.invalid",
        "status": "failed", "raw_data": {"company": "Synthetic"}, "errors": ["synthetic failure"]}]


@pytest.mark.asyncio
async def test_missing_campaign_export_and_download_are_404_and_bad_format_is_non_200(files_app):
    client = files_app.client
    missing = str(uuid.uuid4())
    assert (await client.get(f"/api/campaigns/{missing}/export")).status_code == 404
    assert (await client.get("/api/campaigns/not-a-uuid/export")).status_code == 404
    token = f"{uuid.uuid4()}.csv"
    assert (await client.get(f"/api/campaigns/{missing}/export/download", params={"file": token})).status_code == 404
    assert (await client.get(f"/api/campaigns/{files_app.first.id}/export", params={"format": "exe"})).status_code == 422
    assert (await client.get(f"/api/campaigns/{files_app.first.id}/export/download", params={"file": token})).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [
    "../private.csv", "/tmp/private.csv", "..\\private.csv", "%2e%2e%2fprivate.csv",
    "%252e%252e%252fprivate.csv", "not-a-uuid.csv", f"{uuid.uuid4()}.exe",
    f"{uuid.uuid4()}.csv/../private", f"{uuid.uuid4()}.csv\x00",
])
async def test_download_rejects_arbitrary_names_and_traversal(files_app, token):
    response = await files_app.client.get(
        f"/api/campaigns/{files_app.first.id}/export/download", params={"file": token})
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "root-symlink"])
async def test_download_rejects_non_private_regular_files(files_app, tmp_path, monkeypatch, kind):
    app = files_app
    app.directory.mkdir()
    token = f"{uuid.uuid4()}.csv"
    name = export_basename(app.first.id, token)
    target = tmp_path / "outside-fixture.csv"
    target.write_text("SYNTHETIC-PRIVATE-FILE-NOT-FOR-DOWNLOAD")
    path = app.directory / name
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        link = tmp_path / "linked-storage"
        link.symlink_to(app.directory, target_is_directory=True)
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(link))
        path.write_text("SYNTHETIC-PRIVATE-FILE-NOT-FOR-DOWNLOAD")
    response = await app.client.get(f"/api/campaigns/{app.first.id}/export/download", params={"file": token})
    assert response.status_code == 404
    assert "SYNTHETIC-PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_anonymous_export_download_and_upload_are_denied_before_storage(files_app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Anonymous request reached private file handling")
    monkeypatch.setattr(campaigns, "create_private_file", forbidden)
    monkeypatch.setattr(campaigns, "open_export", forbidden)
    files_app.client.cookies.clear()
    root = f"/api/campaigns/{files_app.first.id}"
    assert (await files_app.client.get(root + "/export")).status_code == 401
    assert (await files_app.client.get(root + "/export/download", params={"file": f"{uuid.uuid4()}.csv"})).status_code == 401
    assert (await files_app.client.post(root + "/upload", files={"file": ("qa.csv", b"email\nqa@example.invalid\n")})).status_code == 401
    assert not files_app.directory.exists()


@pytest.mark.asyncio
async def test_upload_preserves_display_name_generates_unique_private_paths(files_app):
    app = files_app
    filename = "Contacts Q4 – synthetic.CSV"
    paths = []
    for _ in range(2):
        response = await app.client.post(f"/api/campaigns/{app.first.id}/upload", files={
            "file": (filename, b"Name,Email,Phone\nQA,qa@example.invalid,\n", "text/csv")})
        assert response.status_code == 200, response.text
        assert response.json()["filename"] == filename
        assert response.json()["mode"] == "initial"
        assert response.json()["columns"] == ["name", "email", "phone"]
        assert app.first.csv_filename == filename
        path = Path(app.first.csv_storage_path)
        assert path.parent == app.directory
        assert str(uuid.UUID(path.stem)) == path.stem
        assert path.suffix == ".csv"
        assert path.stat().st_mode & 0o777 == 0o600
        assert DataLoader.load_file(str(path)).iloc[0]["phone"] == ""
        paths.append(path)
    assert paths[0] != paths[1]
    assert all(path.exists() for path in paths)


@pytest.mark.asyncio
async def test_excel_upload_is_supported(files_app):
    output = io.BytesIO()
    pd.DataFrame({"Email": ["qa@example.invalid"]}).to_excel(output, index=False)
    response = await files_app.client.post(f"/api/campaigns/{files_app.first.id}/upload", files={
        "file": ("Synthetic.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200, response.text
    assert response.json()["row_count"] == 1
    assert response.json()["columns"] == ["email"]


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", [
    "../contacts.csv", "/tmp/contacts.csv", "safe%2fcontacts.csv",
    "safe%252fcontacts.csv", "contacts.csv:other.csv", "contacts.exe", "contacts.csv.exe", ".csv",
])
async def test_upload_rejects_path_tricks_and_unsupported_extensions(files_app, filename):
    response = await files_app.client.post(f"/api/campaigns/{files_app.first.id}/upload", files={
        "file": (filename, b"email\nqa@example.invalid\n")})
    assert response.status_code == 400, response.text
    assert not files_app.directory.exists()


@pytest.mark.asyncio
async def test_raw_windows_path_is_rejected_and_parser_sanitized_name_stays_in_storage(files_app):
    app = files_app
    windows_name = "C:\\fakepath\\contacts.csv"
    with pytest.raises(HTTPException) as error:
        await campaigns.upload_file(app.first.id, UploadFile(
            filename=windows_name, file=io.BytesIO(b"email\nqa@example.invalid\n")), app.session)
    assert error.value.status_code == 400
    assert not app.directory.exists()
    # python-multipart strips Windows browser path prefixes before UploadFile.
    response = await app.client.post(f"/api/campaigns/{app.first.id}/upload", files={
        "file": (windows_name, b"email\nqa@example.invalid\n")})
    assert response.status_code == 200
    assert response.json()["filename"] == "contacts.csv"
    assert Path(app.first.csv_storage_path).parent == app.directory


@pytest.mark.asyncio
async def test_upload_missing_campaign_and_invalid_data_preserve_errors_and_cleanup(files_app):
    app = files_app
    response = await app.client.post(f"/api/campaigns/{uuid.uuid4()}/upload", files={"file": ("qa.csv", b"email\n")})
    assert response.status_code == 404
    response = await app.client.post(f"/api/campaigns/{app.first.id}/upload", files={"file": ("qa.csv", b"")})
    assert response.status_code == 400
    assert list(app.directory.iterdir()) == []


@pytest.mark.asyncio
async def test_append_semantics_and_schema_error_cleanup_are_preserved(files_app):
    app = files_app
    app.first.inferred_schema_json = {"primary_email_column": "email"}
    app.first.status = CampaignStatus.COMPLETED
    app.session.add(CampaignRow(campaign_id=app.first.id, row_number=1,
        recipient_email="existing@example.invalid", raw_row_json={"email": "existing@example.invalid"}))
    await app.session.commit()
    response = await app.client.post(f"/api/campaigns/{app.first.id}/upload", files={
        "file": ("Append.csv", b"email\nexisting@example.invalid\nnew@example.invalid\n")})
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "append"
    assert response.json()["added_rows"] == 1
    assert response.json()["skipped_duplicates"] == 1
    assert app.first.status == CampaignStatus.PAUSED
    accepted = set(app.directory.iterdir())
    response = await app.client.post(f"/api/campaigns/{app.first.id}/upload", files={
        "file": ("Bad-schema.csv", b"name\nSynthetic\n")})
    assert response.status_code == 400
    assert set(app.directory.iterdir()) == accepted


class ChunkedUpload:
    filename = "qa.csv"
    size = 1  # Deliberately untrusted/incorrect declared size.

    def __init__(self, data, failure=None):
        self.data = io.BytesIO(data)
        self.read_sizes = []
        self.failure = failure

    async def read(self, size):
        self.read_sizes.append(size)
        if self.failure and len(self.read_sizes) > 1:
            raise self.failure
        return self.data.read(size)


@pytest.mark.asyncio
async def test_actual_ten_mb_limit_streams_and_cleans_oversize_partial_file(files_app):
    limit = 10 * 1024 * 1024
    upload = ChunkedUpload(b"x" * (limit + 65536))
    destination = upload_destination("csv")
    with pytest.raises(HTTPException) as error:
        await DataLoader.save_upload(upload, destination)
    assert error.value.status_code == 413
    assert max(upload.read_sizes) <= 65536
    assert upload.data.tell() == limit + 1
    assert not Path(destination).exists()


@pytest.mark.asyncio
async def test_exact_limit_is_accepted_and_http_413_is_not_masked(files_app, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE", 12)
    destination = upload_destination("csv")
    await DataLoader.save_upload(ChunkedUpload(b"a" * 12), destination)
    assert Path(destination).read_bytes() == b"a" * 12
    existing = set(files_app.directory.iterdir())
    response = await files_app.client.post(f"/api/campaigns/{files_app.first.id}/upload", files={
        "file": ("qa.csv", b"email\nqa@example.invalid\n")})
    assert response.status_code == 413, response.text
    assert set(files_app.directory.iterdir()) == existing


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("synthetic read failure"), asyncio.CancelledError()])
async def test_read_failures_and_cancellation_remove_partial_upload(files_app, failure):
    destination = upload_destination("csv")
    with pytest.raises(type(failure)):
        await DataLoader.save_upload(ChunkedUpload(b"x" * 65537, failure), destination)
    assert not Path(destination).exists()


@pytest.mark.asyncio
async def test_upload_writer_rejects_outside_path_symlink_and_overwrite(files_app, tmp_path):
    outside = tmp_path / f"{uuid.uuid4()}.csv"
    upload = UploadFile(filename="qa.csv", file=io.BytesIO(b"synthetic"))
    with pytest.raises(HTTPException) as error:
        await DataLoader.save_upload(upload, str(outside))
    assert error.value.status_code == 400
    assert not outside.exists()
    files_app.directory.mkdir()
    destination = Path(upload_destination("csv"))
    outside.write_text("synthetic protected target")
    destination.symlink_to(outside)
    await upload.seek(0)
    with pytest.raises(HTTPException) as error:
        await DataLoader.save_upload(upload, str(destination))
    assert error.value.status_code == 409
    assert destination.is_symlink()
    assert outside.read_text() == "synthetic protected target"


@pytest.mark.asyncio
async def test_export_propagates_existing_http_errors(files_app, monkeypatch):
    async def denied(*args):
        raise HTTPException(status_code=409, detail="synthetic conflict")
    monkeypatch.setattr(files_app.session, "get", denied)
    response = await files_app.client.get(f"/api/campaigns/{files_app.first.id}/export")
    assert response.status_code == 409
    assert response.json()["detail"] == "synthetic conflict"
