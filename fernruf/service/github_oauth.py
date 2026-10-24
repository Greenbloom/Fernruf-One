"""GitHub login, the consent page, and the OAuth HTTP routes."""

from __future__ import annotations

import html
import json
import logging
import secrets
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from mcp.server.auth.provider import AuthorizationCode, construct_redirect_uri
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl, AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from fernruf.service.oauth_provider import CODE_TTL_S, FernrufOAuthProvider
from fernruf.service.oauth_store import OAuthStore
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.oauth")
GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"
GITHUB_SCOPE = "read:user"


class GitHubLogin:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.settings.github_client_id.strip() and self.settings.github_client_secret.strip())

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.github_client_id,
                "redirect_uri": self.settings.github_callback_url,
                "scope": GITHUB_SCOPE,
                "state": state,
                "allow_signup": "false",
            }
        )
        return f"{GITHUB_AUTHORIZE}?{query}"

    async def user_id(self, code: str) -> str:
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            token_response = await client.post(
                GITHUB_TOKEN,
                data={
                    "client_id": self.settings.github_client_id,
                    "client_secret": self.settings.github_client_secret,
                    "code": code,
                    "redirect_uri": self.settings.github_callback_url,
                },
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            payload = token_response.json()
            access = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(access, str) or not access:
                raise RuntimeError("GitHub did not return a token")
            user_response = await client.get(
                GITHUB_USER,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "fernruf",
                },
            )
            user_response.raise_for_status()
            user = user_response.json()
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(user_id, int):
            raise RuntimeError("GitHub did not return a numeric user id")
        return str(user_id)


def _page(title: str, body: str) -> HTMLResponse:
    document = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )
    return HTMLResponse(document)


def _denial() -> HTMLResponse:
    return _page("fernruf", "<p>This GitHub account is not allowed to use fernruf.</p>")


def _consent(client_name: str, txn_id: str) -> HTMLResponse:
    name = html.escape(client_name[:80] or "this client")
    txn = html.escape(txn_id)
    body = (
        f"<p>Allow <strong>{name}</strong> to place calls with fernruf?</p>"
        "<form method=\"post\" action=\"/oauth/github/callback\">"
        f"<input type=\"hidden\" name=\"txn\" value=\"{txn}\">"
        "<button type=\"submit\" name=\"consent\" value=\"yes\">Allow</button>"
        "</form>"
    )
    return _page("fernruf", body)


def build_github_callback(settings: Settings, store: OAuthStore, github: GitHubLogin):
    async def callback(request: Request) -> Response:
        if request.method == "GET":
            state = request.query_params.get("state", "")
            code = request.query_params.get("code", "")
            txn = store.get_txn(state) if state else None
            if txn is None or txn.get("phase") != "pending_github" or not code:
                return _page("fernruf", "<p>GitHub login failed.</p>")
            try:
                user_id = await github.user_id(code)
            except Exception:
                LOG.info("fernruf oauth github exchange failed")
                return _page("fernruf", "<p>GitHub login failed.</p>")
            if user_id not in settings.github_allowed_ids:
                txn["phase"] = "denied"
                store.update_txn(state, txn)
                LOG.info("fernruf oauth denied github_user_id=%s", user_id)
                return _denial()
            txn["phase"] = "pending_consent"
            txn["github_user_id"] = user_id
            store.update_txn(state, txn)
            return _consent(str(txn.get("client_name") or ""), state)

        form = await request.form()
        txn_id = str(form.get("txn") or "")
        if form.get("consent") != "yes":
            return _page("fernruf", "<p>GitHub login failed.</p>")
        txn = store.get_txn(txn_id)
        if txn is None or txn.get("phase") != "pending_consent":
            return _page("fernruf", "<p>GitHub login failed.</p>")
        txn["phase"] = "done"
        store.update_txn(txn_id, txn)
        raw_code = secrets.token_urlsafe(32)
        store.save_code(
            raw_code,
            AuthorizationCode(
                code=raw_code,
                scopes=list(txn["scopes"]),
                expires_at=time.time() + CODE_TTL_S,
                client_id=str(txn["client_id"]),
                code_challenge=str(txn["code_challenge"]),
                redirect_uri=AnyUrl(str(txn["redirect_uri"])),
                redirect_uri_provided_explicitly=bool(txn["redirect_uri_provided_explicitly"]),
                resource=txn.get("resource"),
                subject=str(txn["github_user_id"]),
            ),
        )
        LOG.info(
            "fernruf oauth code issued github_user_id=%s client_id=%s",
            txn["github_user_id"],
            txn["client_id"],
        )
        location = construct_redirect_uri(
            str(txn["redirect_uri"]),
            code=raw_code,
            state=txn.get("client_state"),
            iss=settings.oauth_issuer,
        )
        return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})

    return callback


def _issuer_without_empty_path(url: str) -> str:
    """Pydantic adds a trailing slash to an origin. RFC 8414 forbids that on an issuer."""
    parsed = urlsplit(url)
    if parsed.path in ("", "/"):
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return url


def _rewrite_issuer_urls(payload: object) -> object:
    if isinstance(payload, dict):
        rewritten = {}
        for key, value in payload.items():
            if key in ("issuer", "authorization_servers") and isinstance(value, str):
                rewritten[key] = _issuer_without_empty_path(value)
            elif key == "authorization_servers" and isinstance(value, list):
                rewritten[key] = [
                    _issuer_without_empty_path(item) if isinstance(item, str) else item for item in value
                ]
            else:
                rewritten[key] = value
        return rewritten
    return payload


class _IssuerJSON:
    """Rewrite issuer origins in a well-known JSON body. Other responses pass through."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started: dict | None = None
        chunks: list[bytes] = []

        async def capture(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body") or b"")
            if message.get("more_body", False):
                return
            raw = b"".join(chunks)
            try:
                payload = _rewrite_issuer_urls(json.loads(raw))
                encoded = json.dumps(payload).encode()
            except (json.JSONDecodeError, UnicodeError):
                encoded = raw
            headers = [
                (key, value)
                for key, value in (started or {}).get("headers", [])
                if key.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(encoded)).encode()))
            await send({**(started or {"type": "http.response.start", "status": 200}), "headers": headers})
            await send({"type": "http.response.body", "body": encoded})

        await self.app(scope, receive, capture)


def oauth_routes(
    settings: Settings,
    store: OAuthStore,
    provider: FernrufOAuthProvider,
    github: GitHubLogin,
) -> list[Route]:
    issuer = AnyHttpUrl(settings.oauth_issuer)
    resource = AnyHttpUrl(settings.oauth_resource)
    routes = create_auth_routes(
        provider,
        issuer,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["calls"],
            default_scopes=["calls"],
        ),
        revocation_options=RevocationOptions(enabled=False),
    )
    routes.extend(
        create_protected_resource_routes(
            resource_url=resource,
            authorization_servers=[issuer],
            scopes_supported=["calls"],
            resource_name="fernruf",
        )
    )
    for route in routes:
        if route.path.startswith("/.well-known/oauth-"):
            # Route.app is fixed at construction. Replacing endpoint alone does not
            # change the ASGI app that serves the request.
            route.app = _IssuerJSON(route.app)
    protected = next(route for route in routes if route.path.startswith("/.well-known/oauth-protected-resource"))
    routes.append(
        Route(
            "/.well-known/oauth-protected-resource",
            endpoint=protected.app,
            methods=["GET", "OPTIONS"],
        )
    )
    routes.append(
        Route(
            "/oauth/github/callback",
            endpoint=build_github_callback(settings, store, github),
            methods=["GET", "POST"],
        )
    )
    return routes
