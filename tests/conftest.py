"""Every test uses disposable local state and forbids provider network calls."""
import atexit
import base64
import os
import socket
import tempfile
from pathlib import Path

import pytest

_state = tempfile.TemporaryDirectory(prefix="outreach-offline-tests-")
atexit.register(_state.cleanup)
for _name in ("OPENAI_API_KEY", "FIREWORKS_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    os.environ[_name] = ""
os.environ.update({
    "DATABASE_URL": "sqlite:///" + str(Path(_state.name) / "outreach.db"),
    "CHECKPOINT_DATABASE_URL": "sqlite:///" + str(Path(_state.name) / "checkpoints.db"),
    "UPLOAD_DIR": str(Path(_state.name) / "uploads"),
    "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"0" * 32).decode(),
    "EMAIL_SENDING_ENABLED": "false", "GMAIL_CONNECTION_ENABLED": "false",
    "APP_LOGIN_REQUIRED": "true", "APP_OWNER_EMAIL": "owner@example.invalid",
    "APP_SESSION_SECRET": "offline-tests-only-session-secret-0123456789",
    "APP_GOOGLE_CLIENT_ID": "offline-client.apps.googleusercontent.com",
    "APP_BASE_URL": "http://127.0.0.1:8000",
})


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    """ASGI tests need no TCP; asyncio's local Unix socketpair still works."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Network access is forbidden in offline outreach tests")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Network access is forbidden in offline outreach tests")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
