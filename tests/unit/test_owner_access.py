"""Offline tests of owner access; never import the real app or campaign services."""

import re
import socket
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import requests
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from google.auth.exceptions import GoogleAuthError
from itsdangerous import TimestampSigner
from starlette.websockets import WebSocketDisconnect

from app.core import owner_access as owner

ORIGIN = "https://outreach.example.test"
OWNER = "sahil@granveo.com"
CLIENT_ID = "offline-test.apps.googleusercontent.com"
SECRET = "offline-only-session-secret-at-least-32-characters"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is prohibited in owner access tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


def settings(**overrides):
    values = {
        "APP_OWNER_EMAIL": OWNER,
        "APP_GOOGLE_CLIENT_ID": CLIENT_ID,
        "APP_SESSION_SECRET": SECRET,
        "APP_BASE_URL": ORIGIN,
        "APP_LOGIN_REQUIRED": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_app(config=None):
    app = FastAPI()
    app.state.private_calls = []

    async def private(request: Request):
        app.state.private_calls.append((request.method, request.url.path))
        return JSONResponse({"private": True}, headers={"Cache-Control": "public, max-age=3600"})

    for path in (
        "/",
        "/auth",
        "/campaigns",
        "/campaigns/1",
        "/campaigns/new",
        "/leads",
        "/api/campaigns",
        "/api/campaigns/1/run",
        "/api/leads",
        "/api/followups/send",
        "/auth/google/start",
        "/auth/google/callback",
        "/auth/google/disconnect",
        "/auth/status",
        "/auth/signature",
        "/new-private-route",
        "/login/not-public",
        "/health/not-public",
    ):
        app.add_api_route(
            path,
            private,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
            include_in_schema=False,
        )

    for path in owner.PUBLIC_PROBES:
        app.add_api_route(
            path, lambda: {"status": "ok"}, methods=["GET", "HEAD"], include_in_schema=False
        )

    @app.get("/failure")
    async def failure():
        raise RuntimeError("synthetic private error")

    @app.websocket("/socket")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text("private")
        await ws.close()

    app.mount(
        "/static", StaticFiles(directory=str(owner.Path(owner.__file__).parents[1] / "static"))
    )
    policy = owner.setup_owner_access(app, config or settings())
    return app, policy


@pytest.fixture
def client():
    app, policy = make_app()
    # Context starts only this isolated app; it has no production lifespan.
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.policy = policy
        yield client


def start_login(client, destination="/campaigns"):
    response = client.get("/login", params={"next": destination})
    assert response.status_code == 200
    nonce = re.search(r'data-nonce="([A-Za-z0-9_-]+)"', response.text).group(1)
    return nonce


def google_claims(browser_nonce, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-owner-123",
        "email": OWNER,
        "email_verified": True,
        "iat": now - 10,
        "exp": now + 600,
        "nonce": browser_nonce,
    }
    claims.update(overrides)
    return claims


def mock_google(monkeypatch, claims=None, error=None):
    verifier = Mock(return_value=claims, side_effect=error)
    monkeypatch.setattr(owner.id_token, "verify_oauth2_token", verifier)
    return verifier


def sign_session(policy, **overrides):
    now = int(time.time())
    payload = {
        "v": 1,
        "email": OWNER,
        "sub": "google-owner-123",
        "iat": now,
        "exp": now + owner.SESSION_SECONDS,
    }
    payload.update(overrides)
    return policy.serializer("session").dumps(payload)


def authenticate(client, **overrides):
    cookie = sign_session(client.policy, **overrides)
    client.cookies.set(client.policy.cookie_name, cookie, domain="outreach.example.test", path="/")
    return cookie


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/auth",
        "/campaigns",
        "/campaigns/1",
        "/campaigns/new",
        "/leads",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
        "/new-private-route",
        "/health/not-public",
    ],
)
def test_private_html_redirects_without_running_handlers(client, path):
    response = client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"] == [path]
    assert "no-store" in response.headers["cache-control"]
    assert client.app.state.private_calls == []


def test_only_google_login_page_allows_origin_referrers(client):
    login = client.get("/login?next=/campaigns/private-record")
    assert login.status_code == 200
    assert login.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "no-store" in login.headers["cache-control"]
    for path in ["/api/campaigns", "/auth/google/callback?code=private-code", "/session"]:
        assert client.get(path).headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "path",
    [
        "/api/campaigns",
        "/api/campaigns/1/run",
        "/api/leads",
        "/api/followups/send",
        "/auth/google/start",
        "/auth/google/callback?code=must-not-forward&state=secret",
        "/auth/google/disconnect",
        "/auth/status",
        "/auth/signature",
        "/openapi.json",
    ],
)
def test_private_api_requires_session(client, path):
    response = client.get(path, headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert "location" not in response.headers
    assert client.app.state.private_calls == []


def test_openapi_uses_api_401_for_default_accept_header(client):
    assert client.get("/openapi.json").status_code == 401


@pytest.mark.parametrize("path", sorted(owner.PUBLIC_PROBES | owner.PUBLIC_ASSETS))
def test_only_exact_probe_and_login_assets_are_public(client, path):
    assert client.get(path).status_code == 200
    assert client.head(path).status_code == 200
    assert client.post(path, headers={"Origin": ORIGIN}).status_code == 401


@pytest.mark.parametrize("path", ["/static/__init__.py", "/static/private.js", "/login/not-public"])
def test_no_public_prefix_bypass(client, path):
    assert client.get(path, headers={"Accept": "application/json"}).status_code == 401


def test_full_login_session_logout_and_public_client_id(client, monkeypatch):
    nonce = start_login(client)
    page = client.get("/login")
    nonce = re.search(r'data-nonce="([A-Za-z0-9_-]+)"', page.text).group(1)
    assert CLIENT_ID in page.text
    assert SECRET not in page.text
    assert OWNER not in page.text
    assert "textarea" not in page.text and 'type="password"' not in page.text
    verifier = mock_google(monkeypatch, google_claims(nonce))
    response = client.post(
        "/login/google",
        headers={"Origin": ORIGIN},
        json={
            "credential": "synthetic-google-id-token",
            "next": "/campaigns?credential=discard",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"redirect": "/campaigns"}
    assert verifier.call_args.kwargs["audience"] == CLIENT_ID
    assert verifier.call_args.args[0] == "synthetic-google-id-token"
    assert verifier.call_args.args[1].keywords["timeout"] == 5
    cookies = response.headers.get_list("set-cookie")
    cookie = next(value for value in cookies if value.startswith("__Host-owner_session="))
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Domain=" not in cookie
    assert f"Max-Age={owner.SESSION_SECONDS}" in cookie
    assert "synthetic-google-id-token" not in response.text + cookie
    assert client.cookies.get(client.policy.challenge_name) is None
    session = client.get("/session").json()
    assert session["authenticated"] is True and session["email"] == OWNER
    assert client.get("/campaigns").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/auth/google/start").status_code == 200
    assert client.post("/api/campaigns/1/run", headers={"Origin": ORIGIN}).status_code == 200
    assert client.post("/logout", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/session").status_code == 401
    assert client.get("/campaigns").status_code == 303


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_another_login_page_does_not_invalidate_pending_signin(client, monkeypatch, method):
    first_nonce = start_login(client)
    original_cookie = client.cookies.get(client.policy.challenge_name)
    another_page = client.request(method, "/login")
    assert another_page.status_code == 200
    assert another_page.headers.get_list("set-cookie") == []
    assert client.cookies.get(client.policy.challenge_name) == original_cookie
    if method == "GET":
        assert f'data-nonce="{first_nonce}"' in another_page.text
    mock_google(monkeypatch, google_claims(first_nonce))
    assert client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
    ).status_code == 200


def test_reloading_login_does_not_extend_original_challenge_lifetime(client, monkeypatch):
    started = int(time.time())
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: started)
    first_nonce = start_login(client)
    original_cookie = client.cookies.get(client.policy.challenge_name)
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: started + 599)
    page = client.get("/login")
    assert page.headers.get_list("set-cookie") == []
    assert client.cookies.get(client.policy.challenge_name) == original_cookie
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: started + 601)
    verifier = mock_google(monkeypatch, google_claims(first_nonce))
    assert client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
    ).status_code == 401
    verifier.assert_not_called()
    assert start_login(client) != first_nonce
    assert client.cookies.get(client.policy.challenge_name) != original_cookie


@pytest.mark.parametrize("payload", [None, [], {}, {"nonce": "short"}, {"nonce": "é" * 43}])
def test_login_page_replaces_invalid_signed_challenges(client, payload):
    bad_cookie = client.policy.serializer("login").dumps(payload)
    response = client.get(
        "/login", headers={"Cookie": f"{client.policy.challenge_name}={bad_cookie}"}
    )
    assert response.status_code == 200
    assert response.headers.get_list("set-cookie")
    assert client.cookies.get(client.policy.challenge_name) != bad_cookie
    assert re.search(r'data-nonce="[A-Za-z0-9_-]{43}"', response.text)


@pytest.mark.parametrize("kind", ["tampered", "different_owner", "future", "duplicate"])
def test_untrusted_challenge_is_rejected_then_replaced(client, monkeypatch, kind):
    policy = client.policy
    signer = policy.serializer("login")
    payload = {"nonce": "x" * 43}
    good = signer.dumps(payload)
    if kind == "tampered":
        value = good + "invalid"
    elif kind == "different_owner":
        value = replace(policy, owner_email="someone-else@example.invalid").serializer(
            "login"
        ).dumps(payload)
    elif kind == "future":
        class FutureSigner(TimestampSigner):
            def get_timestamp(self):
                return int(time.time()) + 60
        signer.signer = FutureSigner
        value = signer.dumps(payload)
    else:
        value = f"{good}; {policy.challenge_name}={good}"
    cookie_header = f"{policy.challenge_name}={value}"
    verifier = mock_google(monkeypatch, google_claims(payload["nonce"]))
    assert client.post(
        "/login/google", headers={"Origin": ORIGIN, "Cookie": cookie_header},
        json={"credential": "fake"},
    ).status_code == 401
    verifier.assert_not_called()
    page = client.get("/login", headers={"Cookie": cookie_header})
    assert page.status_code == 200 and page.headers.get_list("set-cookie")
    assert f'data-nonce="{payload["nonce"]}"' not in page.text


def test_competing_initial_pages_recover_by_reloading(client, monkeypatch, caplog):
    stale_nonce = start_login(client)
    client.cookies.clear()  # Another concurrent initial page had no cookie yet.
    current_nonce = start_login(client)
    current_cookie = client.cookies.get(client.policy.challenge_name)
    mock_google(monkeypatch, google_claims(stale_nonce))
    response = client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Reload the login page and try again"
    assert client.cookies.get(client.policy.cookie_name) is None
    assert client.cookies.get(client.policy.challenge_name) == current_cookie
    assert "nonce_mismatch" in caplog.text
    assert stale_nonce not in caplog.text and current_nonce not in caplog.text
    assert start_login(client) == current_nonce
    mock_google(monkeypatch, google_claims(current_nonce))
    assert client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
    ).status_code == 200


@pytest.mark.parametrize(
    "claims_patch",
    [
        {"iss": "https://evil.test"},
        {"iss": []},
        {"aud": "other.apps.googleusercontent.com"},
        {"aud": [CLIENT_ID]},
        {"azp": "other-client"},
        {"email_verified": False},
        {"email_verified": "false"},
        {"email_verified": "True"},
        {"email_verified": 1},
        {"email": None},
        {"sub": ""},
        {"sub": None},
        {"sub": "contains spaces"},
        {"sub": "x" * 256},
        {"nonce": "wrong-browser"},
        {"nonce": None},
        {"nonce": "é" * 43},
        {"nonce": "\ud800" * 43},
        {"exp": 1},
        {"exp": "9999999999"},
        {"iat": True},
        {"iat": 9999999999},
    ],
)
def test_claims_fail_closed(client, monkeypatch, claims_patch):
    nonce = start_login(client)
    mock_google(monkeypatch, google_claims(nonce, **claims_patch))
    response = client.post("/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"})
    assert response.status_code == 401
    assert client.cookies.get(client.policy.cookie_name) is None


@pytest.mark.parametrize("issuer", owner.GOOGLE_ISSUERS)
def test_accepted_google_issuers_and_case_insensitive_owner(client, monkeypatch, issuer):
    nonce = start_login(client)
    mock_google(monkeypatch, google_claims(nonce, iss=issuer, email=OWNER.upper()))
    assert (
        client.post(
            "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
        ).status_code
        == 200
    )


def test_other_verified_account_is_forbidden_without_registration(client, monkeypatch):
    nonce = start_login(client)
    mock_google(monkeypatch, google_claims(nonce, email="someone-else@granveo.com"))
    assert (
        client.post(
            "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
        ).status_code
        == 403
    )
    assert client.cookies.get(client.policy.cookie_name) is None


@pytest.mark.parametrize(
    "error", [ValueError("sensitive-token"), GoogleAuthError("sensitive-token")]
)
def test_verification_errors_are_generic_and_not_logged(client, monkeypatch, caplog, error):
    start_login(client)
    mock_google(monkeypatch, error=error)
    response = client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": "sensitive-token"}
    )
    assert response.status_code == 401
    assert "sensitive-token" not in response.text + caplog.text


def test_login_requires_fresh_browser_challenge(client, monkeypatch):
    verifier = mock_google(monkeypatch, google_claims("nonce"))
    assert (
        client.post(
            "/login/google", headers={"Origin": ORIGIN}, json={"credential": "fake"}
        ).status_code
        == 401
    )
    start_login(client)
    old = client.policy.serializer("login")

    class OldSigner(TimestampSigner):
        def get_timestamp(self):
            return int(time.time()) - owner.LOGIN_SECONDS - 1

    old.signer = OldSigner
    challenge = old.dumps({"nonce": "x" * 43})
    response = client.post(
        "/login/google",
        headers={"Origin": ORIGIN, "Cookie": f"{client.policy.challenge_name}={challenge}"},
        json={"credential": "fake"},
    )
    assert response.status_code == 401
    verifier.assert_not_called()


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://evil.test",
        ORIGIN + "/",
        ORIGIN + ".evil.test",
        "http://outreach.example.test",
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "/login/google",
        "/logout",
        "/api/campaigns/1/run",
        "/auth/google/disconnect",
        "/auth/signature",
    ],
)
def test_all_unsafe_routes_require_exact_origin(client, monkeypatch, origin, path):
    authenticate(client)
    verifier = mock_google(monkeypatch)
    headers = {} if origin is None else {"Origin": origin}
    response = client.post(path, headers=headers, json={"credential": "fake"})
    assert response.status_code == 403
    assert client.app.state.private_calls == []
    verifier.assert_not_called()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_origin_enforced_for_each_unsafe_method(client, method):
    authenticate(client)
    assert client.request(method, "/auth/signature").status_code == 403
    assert client.request(method, "/auth/signature", headers={"Origin": ORIGIN}).status_code == 200


def test_forged_host_and_forwarded_headers_cannot_redefine_origin(client):
    authenticate(client)
    response = client.post(
        "/auth/google/disconnect",
        headers={
            "Origin": "https://evil.test",
            "Host": "evil.test",
            "Forwarded": "host=evil.test;proto=https",
            "X-Forwarded-Host": "evil.test",
            "X-Forwarded-Proto": "https",
        },
    )
    assert response.status_code == 403
    assert client.app.state.private_calls == []
    # A normal TLS-terminating proxy may pass an internal Host and scheme.
    assert (
        client.post(
            "/auth/google/disconnect",
            headers={
                "Origin": ORIGIN,
                "Host": "internal:8000",
                "X-Forwarded-Proto": "http",
            },
        ).status_code
        == 200
    )


def test_multiple_origin_headers_are_rejected(client):
    authenticate(client)
    assert (
        client.post("/logout", headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]).status_code == 403
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "other@example.test"},
        {"v": 2},
        {"sub": ""},
        {"iat": 9999999999},
        {"iat": 1, "exp": 1 + owner.SESSION_SECONDS},
        {"exp": 9999999999},
        {"iat": True},
        {"exp": "9999999999"},
    ],
)
def test_signed_but_invalid_sessions_are_rejected(client, overrides):
    authenticate(client, **overrides)
    assert client.get("/session").status_code == 401
    assert client.get("/auth/status").status_code == 401


def test_tampered_wrong_key_and_ambiguous_cookies_fail_closed(client):
    good = sign_session(client.policy)
    bad_key = sign_session(
        replace(client.policy, secret="another-offline-secret-with-32-characters")
    )
    for value in (good[:-5] + "xxxxx", bad_key, "garbage", "x" * 5000):
        response = client.get(
            "/session", headers={"Cookie": f"{client.policy.cookie_name}={value}"}
        )
        assert response.status_code == 401
    for header in (
        f"{client.policy.cookie_name}={good}; {client.policy.cookie_name}={good}",
        f"{client.policy.cookie_name}=bad; {client.policy.cookie_name}={good}",
    ):
        assert client.get("/session", headers={"Cookie": header}).status_code == 401


def test_session_signature_has_absolute_age_limit(client):
    serializer = client.policy.serializer("session")

    class OldSigner(TimestampSigner):
        def get_timestamp(self):
            return int(time.time()) - owner.SESSION_SECONDS - 1

    serializer.signer = OldSigner
    now = int(time.time())
    value = serializer.dumps(
        {"v": 1, "email": OWNER, "sub": "owner", "iat": now, "exp": now + owner.SESSION_SECONDS}
    )
    assert (
        client.get(
            "/session", headers={"Cookie": f"{client.policy.cookie_name}={value}"}
        ).status_code
        == 401
    )


def test_changing_owner_client_or_origin_revokes_existing_session(client):
    for changed in (
        replace(client.policy, owner_email="other@example.test"),
        replace(client.policy, client_id="other.apps.googleusercontent.com"),
        replace(client.policy, origin="https://other.example.test"),
    ):
        value = sign_session(changed)
        assert (
            client.get(
                "/session", headers={"Cookie": f"{client.policy.cookie_name}={value}"}
            ).status_code
            == 401
        )


def test_authenticated_responses_never_cache_or_renew_session(client):
    authenticate(client)
    for path in ("/campaigns", "/docs", "/openapi.json", "/session", "/does-not-exist"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "set-cookie" not in response.headers


def test_unhandled_private_error_is_also_noncacheable():
    app, policy = make_app()
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        response = client.get(
            "/failure", headers={"Cookie": f"{policy.cookie_name}={sign_session(policy)}"}
        )
        assert response.status_code == 500
        assert "no-store" in response.headers["cache-control"]
        assert "synthetic private error" not in response.text


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.test/path",
        "//evil.test",
        "/\\evil.test",
        "/%2f%2fevil.test",
        "/%252f%252fevil.test",
        "/..//evil.test",
        "/%2e%2e//evil.test",
        "/campaigns/../login",
        "/campaigns/%2e%2e/login",
        "/%5cevil.test",
        "/%0d%0aLocation:evil",
        "/\n/evil.test",
        "/login",
        "/login/google",
        "/logout",
        "javascript:alert(1)",
        "x" * 3000,
    ],
)
def test_unsafe_redirect_destinations_are_rejected(value):
    assert owner.safe_next(value) == "/"


def test_redirects_keep_only_local_path_and_escape_template(client):
    assert owner.safe_next("/campaigns/123?code=secret#token") == "/campaigns/123"
    assert owner.safe_next("/leads") == "/leads"
    response = client.get("/login", params={"next": '/campaigns/"><script>alert(1)</script>'})
    assert "<script>alert(1)</script>" not in response.text
    response = client.get("/campaigns?code=secret", headers={"Accept": "text/html"})
    assert "secret" not in response.headers["location"]


@pytest.mark.parametrize("path", ["/login/google", "/logout"])
def test_login_and_logout_origin_required_without_any_session(client, monkeypatch, path):
    verifier = mock_google(monkeypatch)
    response = client.post(path, json={"credential": "fake"})
    assert response.status_code == 403
    verifier.assert_not_called()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_private_api_unauthenticated_is_401_before_any_action(client, method):
    assert client.request(method, "/auth/signature").status_code == 401
    assert client.app.state.private_calls == []


def test_google_transport_error_is_generic(client, monkeypatch, caplog):
    start_login(client)
    mock_google(monkeypatch, error=requests.ConnectionError("sensitive-token"))
    response = client.post(
        "/login/google", headers={"Origin": ORIGIN}, json={"credential": "sensitive-token"}
    )
    assert response.status_code == 503
    assert "sensitive-token" not in response.text + caplog.text


@pytest.mark.parametrize(
    "body,content_type,status",
    [
        (b"x" * (owner.MAX_LOGIN_BYTES + 1), "application/json", 413),
        (b"not json", "application/json", 400),
        (b"[]", "application/json", 400),
        (b'{"credential": null}', "application/json", 400),
        (b'{"credential": ""}', "application/json", 400),
        (b"credential=fake", "application/x-www-form-urlencoded", 415),
    ],
)
def test_login_body_is_bounded_and_strict(client, monkeypatch, body, content_type, status):
    verifier = mock_google(monkeypatch)
    response = client.post(
        "/login/google", content=body, headers={"Origin": ORIGIN, "Content-Type": content_type}
    )
    assert response.status_code == status
    verifier.assert_not_called()


def test_websockets_require_session_and_origin(client):
    for headers in ({}, {"Origin": ORIGIN}):
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("wss://outreach.example.test/socket", headers=headers),
        ):
            pass
    authenticate(client)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "wss://outreach.example.test/socket", headers={"Origin": "https://evil.test"}
        ),
    ):
        pass
    with client.websocket_connect(
        "wss://outreach.example.test/socket", headers={"Origin": ORIGIN}
    ) as ws:
        assert ws.receive_text() == "private"


@pytest.mark.parametrize(
    "base_url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"]
)
def test_local_development_cookie_names_and_flags(base_url):
    app, policy = make_app(settings(APP_BASE_URL=base_url))
    with TestClient(app, base_url=base_url) as client:
        response = client.get("/login")
        cookie = response.headers["set-cookie"]
        assert policy.cookie_name == "owner_session"
        assert cookie.startswith("owner_login=")
        assert "Secure" not in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


@pytest.mark.parametrize(
    "overrides",
    [
        {"APP_OWNER_EMAIL": ""},
        {"APP_OWNER_EMAIL": "owner"},
        {"APP_OWNER_EMAIL": "a@ b.test"},
        {"APP_SESSION_SECRET": "short"},
        {"APP_SESSION_SECRET": " " * 32},
        {"APP_GOOGLE_CLIENT_ID": ""},
        {"APP_BASE_URL": ""},
        {"APP_BASE_URL": "http://outreach.example.test"},
        {"APP_BASE_URL": "/relative"},
        {"APP_BASE_URL": "https://outreach.example.test/path"},
        {"APP_BASE_URL": "https://user:password@outreach.example.test"},
        {"APP_BASE_URL": "https://outreach.example.test/?query=1"},
        {"APP_BASE_URL": "https://outreach.example.test/#fragment"},
        {"APP_BASE_URL": "https://outreach.example.test:bad"},
        {"APP_LOGIN_REQUIRED": "False"},
    ],
)
def test_bad_configuration_fails_at_installation(overrides):
    with pytest.raises(ValueError):
        owner.setup_owner_access(FastAPI(), settings(**overrides))


def test_login_required_defaults_true_and_disable_is_explicit():
    config = settings()
    del config.APP_LOGIN_REQUIRED
    assert owner.setup_owner_access(FastAPI(), config) is not None
    app = FastAPI()
    assert owner.setup_owner_access(app, SimpleNamespace(APP_LOGIN_REQUIRED=False)) is None
    assert not app.user_middleware


def test_setup_is_single_install_and_stores_policy():
    app, policy = make_app()
    assert app.state.owner_access is policy
    with pytest.raises(RuntimeError, match="already installed"):
        owner.setup_owner_access(app, settings())
