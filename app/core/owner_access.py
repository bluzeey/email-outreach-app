"""Owner-only app access, independent of the existing Gmail authorization flow.

Integration: call ``setup_owner_access(app)`` before serving requests. Settings
are read only at installation; a secret/client/owner/origin change revokes cookies.
There is no account registration or persistent Google token storage here.
"""

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from itsdangerous import BadData, URLSafeTimedSerializer
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_SECONDS = 8 * 60 * 60
LOGIN_SECONDS = 10 * 60
MAX_LOGIN_BYTES = 16 * 1024
PUBLIC_PROBES = frozenset({"/health", "/ready", "/readyz", "/readiness"})
PUBLIC_ASSETS = frozenset({"/static/style.css", "/static/owner-login.js"})
LOGIN_PATHS = frozenset({"/login", "/login/google", "/session", "/logout"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def safe_next(value: str | None) -> str:
    """Return a local path only; never propagate query strings or fragments."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return "/"
    candidate = value
    for _ in range(4):
        if (
            not candidate.startswith("/")
            or candidate.startswith("//")
            or "\\" in candidate
            or any(ord(char) <= 32 or ord(char) == 127 for char in candidate)
        ):
            return "/"
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return "/"
        if (
            parsed.scheme
            or parsed.netloc
            or "//" in parsed.path
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))
            or parsed.path.rstrip("/") in LOGIN_PATHS
        ):
            return "/"
        decoded = unquote(candidate)
        if decoded == candidate:
            return urlsplit(value).path
        candidate = decoded
    return "/"


@dataclass(frozen=True)
class OwnerAccess:
    """Validated, fixed app identity and signing configuration."""

    owner_email: str
    client_id: str
    origin: str
    secret: str = field(repr=False)

    @classmethod
    def from_settings(cls, settings: Any) -> "OwnerAccess":
        owner = getattr(settings, "APP_OWNER_EMAIL", "")
        client_id = getattr(settings, "APP_GOOGLE_CLIENT_ID", "")
        secret = getattr(settings, "APP_SESSION_SECRET", "")
        base_url = getattr(settings, "APP_BASE_URL", "")
        if not isinstance(owner, str) or owner.count("@") != 1 or len(owner) > 254:
            raise ValueError("APP_OWNER_EMAIL must be the single owner's email")
        if any(char.isspace() or ord(char) < 32 for char in owner) or not all(owner.split("@")):
            raise ValueError("APP_OWNER_EMAIL must be the single owner's email")
        if not isinstance(secret, str) or len(secret) < 32 or not secret.strip():
            raise ValueError("APP_SESSION_SECRET must contain at least 32 characters")
        if (
            not isinstance(client_id, str)
            or not client_id.endswith(".apps.googleusercontent.com")
            or any(char.isspace() for char in client_id)
        ):
            raise ValueError("APP_GOOGLE_CLIENT_ID must be a Google web client ID")
        try:
            parsed = urlsplit(base_url)
            valid = (
                parsed.scheme in {"https", "http"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and not any(char.isspace() or char in "\\%" for char in base_url)
            )
            port = parsed.port
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("APP_BASE_URL must be an absolute origin without a path")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("APP_BASE_URL requires HTTPS except on loopback")
        hostname = parsed.hostname.lower()
        authority = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None and port != {"http": 80, "https": 443}[parsed.scheme]:
            authority += f":{port}"
        return cls(owner.casefold(), client_id, f"{parsed.scheme}://{authority}", secret)

    @property
    def secure(self) -> bool:
        # The configured public origin is authoritative behind a reverse proxy.
        return self.origin.startswith("https://")

    @property
    def cookie_name(self) -> str:
        return "__Host-owner_session" if self.secure else "owner_session"

    @property
    def challenge_name(self) -> str:
        return "__Host-owner_login" if self.secure else "owner_login"

    def serializer(self, purpose: str) -> URLSafeTimedSerializer:
        binding = json.dumps([purpose, self.origin, self.client_id, self.owner_email])
        return URLSafeTimedSerializer(
            self.secret,
            salt=f"email-outreach-owner-v1:{binding}",
            signer_kwargs={"digest_method": hashlib.sha256},
        )

    def set_cookie(self, response: Response, name: str, value: str, seconds: int) -> None:
        response.set_cookie(
            name,
            value,
            max_age=seconds,
            path="/",
            secure=self.secure,
            httponly=True,
            samesite="lax",
        )

    def delete_cookie(self, response: Response, name: str) -> None:
        response.delete_cookie(name, path="/", secure=self.secure, httponly=True, samesite="lax")

    def read_cookie(self, connection: HTTPConnection, name: str) -> str | None:
        # Ambiguous duplicates fail closed, including on HTTP development origins.
        values = []
        for header in connection.headers.getlist("cookie"):
            for item in header.split(";"):
                key, separator, value = item.strip().partition("=")
                if separator and key == name:
                    values.append(value)
        if len(values) != 1 or not values[0] or len(values[0]) > 4096:
            return None
        return values[0]

    def session(self, connection: HTTPConnection) -> dict[str, Any] | None:
        cookie = self.read_cookie(connection, self.cookie_name)
        if not cookie:
            return None
        try:
            data = self.serializer("session").loads(cookie, max_age=SESSION_SECONDS)
        except BadData:
            return None
        now = int(time.time())
        if (
            not isinstance(data, dict)
            or data.get("v") != 1
            or data.get("email") != self.owner_email
            or not _valid_subject(data.get("sub"))
            or type(data.get("iat")) is not int
            or type(data.get("exp")) is not int
            or not data["iat"] <= now < data["exp"]
            or data["exp"] - data["iat"] != SESSION_SECONDS
        ):
            return None
        return data

    def same_origin(self, connection: HTTPConnection) -> bool:
        # Never derive the expected origin from Host, Forwarded, or X-Forwarded-*.
        return connection.headers.getlist("origin") == [self.origin]


def _valid_subject(subject: Any) -> bool:
    return (
        isinstance(subject, str)
        and 1 <= len(subject) <= 255
        and all("!" <= char <= "~" for char in subject)
    )


def _valid_login_nonce(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 43 and value.isascii()


def _claims_rejection(claims: Any, access: OwnerAccess, nonce: str, now: int) -> str | None:
    """Return fixed diagnostic labels, never token contents or account details."""
    if not isinstance(claims, dict):
        return "claims_type"
    if claims.get("iss") not in GOOGLE_ISSUERS:
        return "issuer"
    if claims.get("aud") != access.client_id:
        return "audience"
    if claims.get("azp", access.client_id) != access.client_id:
        return "presenter"
    # Google's signed OIDC claims document both JSON true and the string "true".
    # An integer 1 or any other truthy value is still rejected.
    verified = claims.get("email_verified")
    if verified is not True and not (type(verified) is str and verified == "true"):
        return "email_unverified"
    if not isinstance(claims.get("email"), str) or not _valid_subject(claims.get("sub")):
        return "identity_fields"
    if type(claims.get("iat")) is not int or type(claims.get("exp")) is not int:
        return "timestamp_type"
    if claims["iat"] > now:
        return "issued_in_future"
    if claims["exp"] <= now:
        return "expired"
    if not _valid_login_nonce(claims.get("nonce")):
        return "nonce_missing_or_invalid"
    if not secrets.compare_digest(claims["nonce"], nonce):
        return "nonce_mismatch"
    return None


def _google_rejection(error: Exception) -> str:
    """Classify library failures without logging their data-bearing messages."""
    message = str(error)
    for prefix, reason in (
        ("Token used too early", "issued_in_future"),
        ("Token expired", "expired"),
        ("Wrong recipient", "audience"),
        ("Token has wrong audience", "audience"),
        ("Wrong issuer", "issuer"),
        ("Certificate for key id", "signing_key"),
        ("Could not verify token signature", "signature"),
    ):
        if message.startswith(prefix):
            return reason
    return "google_token_validation"


class OwnerAccessMiddleware:
    """Default-deny every route and mount, including future routes and sockets."""

    def __init__(self, app: ASGIApp, access: OwnerAccess):
        self.app = app
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        session = self.access.session(connection)
        scope.setdefault("state", {})["owner_session"] = session
        if scope["type"] == "websocket":
            if not session or not self.access.same_origin(connection):
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        method = scope["method"]
        public = (
            (method in {"GET", "HEAD"} and path in PUBLIC_PROBES | PUBLIC_ASSETS)
            or (path == "/login" and method in {"GET", "HEAD"})
            or (path == "/session" and method in {"GET", "HEAD"})
            or (path in {"/login/google", "/logout"} and method == "POST")
        )

        response_started = False

        async def private_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private, max-age=0"
                headers["Pragma"] = "no-cache"
                headers["Expires"] = "0"
                # Google Identity Services needs the public login origin. Cross-origin
                # requests expose no login path/query; private responses suppress all referrers.
                headers["Referrer-Policy"] = (
                    "strict-origin-when-cross-origin" if path == "/login" else "no-referrer"
                )
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        if not public and session is None:
            # /auth is an HTML page; other /auth/* routes are Gmail APIs/redirects.
            api = path in {"/api", "/openapi.json"} or path.startswith(("/api/", "/auth/"))
            if (
                method in {"GET", "HEAD"}
                and not api
                and not path.startswith("/static/")
                and ("application/json" not in connection.headers.get("accept", ""))
            ):
                response = RedirectResponse("/login?" + urlencode({"next": safe_next(path)}), 303)
            else:
                response = JSONResponse({"detail": "Owner login required"}, 401)
            await response(scope, receive, private_send)
            return
        if method not in SAFE_METHODS and not self.access.same_origin(connection):
            await JSONResponse({"detail": "Same-origin request required"}, 403)(
                scope, receive, private_send
            )
            return
        try:
            await self.app(scope, receive, private_send)
        except Exception:
            # Starlette's outer 500 handler cannot apply our cache headers itself.
            if not response_started:
                await JSONResponse({"detail": "Internal server error"}, 500)(
                    scope, receive, private_send
                )
            raise


def _verify_google(credential: str, client_id: str) -> dict[str, Any]:
    """Google's verifier checks signatures and expiry; bound certificate fetch time."""
    with requests.Session() as transport_session:
        transport = partial(GoogleRequest(session=transport_session), timeout=5)
        return id_token.verify_oauth2_token(credential, transport, audience=client_id)


def setup_owner_access(app: FastAPI, settings: Any = None) -> OwnerAccess | None:
    """Install before startup; Settings' APP_LOGIN_REQUIRED defaults to True.

    Returns the validated policy (also app.state.owner_access). An explicit False
    disables installation for local development. No existing routes are imported.
    """
    if getattr(app.state, "owner_access_installed", False):
        raise RuntimeError("Owner access is already installed")
    if settings is None:
        from app.core.config import settings

    required = getattr(settings, "APP_LOGIN_REQUIRED", True)
    if type(required) is not bool:
        raise ValueError("APP_LOGIN_REQUIRED must be a boolean")
    if not required:
        return None
    access = OwnerAccess.from_settings(settings)
    app.add_middleware(OwnerAccessMiddleware, access=access)

    @app.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
    async def owner_login(request: Request) -> Response:
        destination = safe_next(request.query_params.get("next"))
        if request.state.owner_session:
            return RedirectResponse(destination, 303)
        nonce = secrets.token_urlsafe(32)
        response = templates.TemplateResponse(
            request,
            "owner_login.html",
            {"client_id": access.client_id, "login_nonce": nonce, "next_path": destination},
        )
        access.set_cookie(
            response,
            access.challenge_name,
            access.serializer("login").dumps({"nonce": nonce}),
            LOGIN_SECONDS,
        )
        return response

    @app.post("/login/google", include_in_schema=False)
    async def owner_google_login(request: Request) -> Response:
        # Origin has already been checked by middleware, including before login.
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"detail": "JSON body required"}, 415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_LOGIN_BYTES:
                return JSONResponse({"detail": "Login request too large"}, 413)
            body.extend(chunk)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return JSONResponse({"detail": "Invalid login request"}, 400)
        if not isinstance(data, dict) or not isinstance(data.get("credential"), str):
            return JSONResponse({"detail": "Google credential required"}, 400)
        if not 1 <= len(data["credential"]) <= MAX_LOGIN_BYTES:
            return JSONResponse({"detail": "Google credential required"}, 400)
        try:
            challenge = access.serializer("login").loads(
                access.read_cookie(request, access.challenge_name) or "", max_age=LOGIN_SECONDS
            )
            if not isinstance(challenge, dict) or not _valid_login_nonce(challenge.get("nonce")):
                raise ValueError("Invalid challenge")
        except (BadData, ValueError):
            return JSONResponse({"detail": "Reload the login page and try again"}, 401)
        try:
            claims = await run_in_threadpool(_verify_google, data["credential"], access.client_id)
        except (ValueError, GoogleAuthError) as error:
            logger.warning("Owner sign-in rejected: %s", _google_rejection(error))
            return JSONResponse({"detail": "Google sign-in could not be verified"}, 401)
        except requests.RequestException:
            logger.warning("Owner sign-in rejected: certificate_transport")
            return JSONResponse({"detail": "Google sign-in is temporarily unavailable"}, 503)

        now = int(time.time())
        rejection = _claims_rejection(claims, access, challenge["nonce"], now)
        if rejection:
            logger.warning("Owner sign-in rejected: %s", rejection)
            return JSONResponse({"detail": "Google sign-in could not be verified"}, 401)
        if claims["email"].casefold() != access.owner_email:
            return JSONResponse({"detail": "This account cannot access this private app"}, 403)

        response = JSONResponse({"redirect": safe_next(data.get("next"))})
        session = {
            "v": 1,
            "email": access.owner_email,
            "sub": claims["sub"],
            "iat": now,
            "exp": now + SESSION_SECONDS,
        }
        access.set_cookie(
            response,
            access.cookie_name,
            access.serializer("session").dumps(session),
            SESSION_SECONDS,
        )
        access.delete_cookie(response, access.challenge_name)
        return response

    @app.api_route("/session", methods=["GET", "HEAD"], include_in_schema=False)
    async def owner_session(request: Request) -> Response:
        if not request.state.owner_session:
            return JSONResponse({"authenticated": False}, 401)
        return JSONResponse(
            {
                "authenticated": True,
                "email": access.owner_email,
                "expires_at": request.state.owner_session["exp"],
            }
        )

    @app.post("/logout", include_in_schema=False)
    async def owner_logout() -> Response:
        response = JSONResponse({"redirect": "/login"})
        access.delete_cookie(response, access.cookie_name)
        access.delete_cookie(response, access.challenge_name)
        return response

    app.state.owner_access = access
    app.state.owner_access_installed = True
    return access
