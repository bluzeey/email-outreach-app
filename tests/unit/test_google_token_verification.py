"""Real RSA/Google-library verification; only public certificate transport is fake."""

import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.auth import crypt, jwt

from app.core import owner_access as owner
from tests.unit.test_owner_access import ORIGIN, google_claims, make_app, start_login


@pytest.fixture
def client():
    app, policy = make_app()
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as browser:
        browser.policy = policy
        yield browser


@pytest.fixture(scope="module")
def signing_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return crypt.RSASigner.from_string(pem, key_id="offline-signing-key"), public.decode()


def configure_certificate_transport(monkeypatch, public):
    request = Mock(
        return_value=SimpleNamespace(
            status=200, data=json.dumps({"offline-signing-key": public}).encode()
        )
    )
    monkeypatch.setattr(owner, "GoogleRequest", lambda session: request)
    return request


@pytest.mark.parametrize("verified", [True, "true"])
def test_google_library_accepts_both_documented_verified_claims(
    client, monkeypatch, signing_key, verified
):
    signer, public = signing_key
    transport = configure_certificate_transport(monkeypatch, public)
    nonce = start_login(client)
    token = jwt.encode(signer, google_claims(nonce, email_verified=verified)).decode()
    response = client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": token}
    )
    assert response.status_code == 200
    assert client.get("/session").json()["authenticated"] is True
    assert token not in response.text + str(response.headers)
    transport.assert_called_once_with(
        "https://www.googleapis.com/oauth2/v1/certs", method="GET", timeout=5
    )


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"aud": "different.apps.googleusercontent.com"}, "audience"),
        ({"nonce": "x" * 43}, "nonce_mismatch"),
        ({"email_verified": False}, "email_unverified"),
        ({"email_verified": "false"}, "email_unverified"),
        ({"email_verified": "True"}, "email_unverified"),
        ({"email_verified": 1}, "email_unverified"),
        ({"exp": 1}, "expired"),
    ],
)
def test_real_signed_invalid_tokens_remain_rejected_without_data_in_logs(
    client, monkeypatch, signing_key, patch, reason, caplog
):
    signer, public = signing_key
    configure_certificate_transport(monkeypatch, public)
    nonce = start_login(client)
    token = jwt.encode(signer, google_claims(nonce, **patch)).decode()
    response = client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": token}
    )
    assert response.status_code == 401
    assert client.cookies.get(client.policy.cookie_name) is None
    assert reason in caplog.text
    assert all(value not in caplog.text for value in [token, nonce, "sahil@granveo.com"])


def test_real_signature_tampering_is_rejected(client, monkeypatch, signing_key, caplog):
    signer, public = signing_key
    configure_certificate_transport(monkeypatch, public)
    nonce = start_login(client)
    encoded = jwt.encode(signer, google_claims(nonce)).decode().split(".")
    payload = google_claims(nonce, exp=int(time.time()) + 86400)
    encoded[1] = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = ".".join(encoded)
    response = client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": token}
    )
    assert response.status_code == 401
    assert client.cookies.get(client.policy.cookie_name) is None
    assert token not in response.text + caplog.text
