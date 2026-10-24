"""OAuth 2.1 with a mocked GitHub login. No real network and no SIP."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.service.github_oauth import GitHubLogin
from fernruf.service.mcp_app import create_app
from fernruf.service.oauth_store import token_hash
from fernruf.service.security import AuthConfigError
from tests.m3_support import fresh_token, service_settings

GITHUB_TOKEN = "gho_SHOULD_NOT_BE_STORED"
GITHUB_SECRET = "ghsec_SHOULD_NOT_BE_STORED"
REDIRECT = "http://127.0.0.1:9/callback"


def _github(user_id: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": GITHUB_TOKEN, "token_type": "bearer"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": user_id, "login": "example-operator"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _app(tmp_path, *, user_id: int = 99999999, token: str | None = None, **overrides):
    settings, static = service_settings(
        tmp_path,
        token=fresh_token() if token is None else token,
        FERNRUF_GITHUB_CLIENT_ID="iv1.github-app",
        FERNRUF_GITHUB_CLIENT_SECRET=GITHUB_SECRET,
        **overrides,
    )
    github = GitHubLogin(settings, transport=_github(user_id))
    return create_app(settings, github=github), settings, static


def _client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.8", 443))
    return httpx.AsyncClient(transport=transport, base_url="http://fernruf.example.com")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _register(client: httpx.AsyncClient, redirect: str = REDIRECT, name: str = "Example MCP Client"):
    return client.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "calls",
        },
    )


async def _until_tokens(app, *, user_id: int = 99999999):
    verifier, challenge = _pkce()
    async with _client(app) as client:
        registered = await _register(client)
        assert registered.status_code == 201, registered.text
        client_id = registered.json()["client_id"]
        started = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "client-state",
                "scope": "calls",
                "resource": "https://fernruf.example.com/mcp",
            },
            follow_redirects=False,
        )
        assert started.status_code == 302, started.text
        github = urlparse(started.headers["location"])
        assert github.netloc == "github.com"
        github_qs = parse_qs(github.query)
        assert github_qs["scope"] == ["read:user"]
        txn = github_qs["state"][0]
        consent = await client.get(
            "/oauth/github/callback",
            params={"code": "github-code", "state": txn},
        )
        assert consent.status_code == 200
        assert GITHUB_TOKEN not in consent.text
        assert GITHUB_SECRET not in consent.text
        if user_id != 99999999:
            return {"denied": consent.text}
        assert "Example MCP Client" in consent.text
        agreed = await client.post(
            "/oauth/github/callback",
            data={"txn": txn, "consent": "yes"},
            follow_redirects=False,
        )
        assert agreed.status_code == 302, agreed.text
        back = urlparse(agreed.headers["location"])
        returned = parse_qs(back.query)
        assert returned["state"] == ["client-state"]
        code = returned["code"][0]
        tokens = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": "https://fernruf.example.com/mcp",
            },
        )
        assert tokens.status_code == 200, tokens.text
        body = tokens.json()
        return {
            "client": client,
            "client_id": client_id,
            "code": code,
            "body": body,
            "db": app.state.runtime.settings.call_settings_db,
        }


def test_metadata_and_pkce_are_required(tmp_path) -> None:
    app, _settings, _static = _app(tmp_path)

    async def run() -> None:
        async with _client(app) as client:
            protected = await client.get("/.well-known/oauth-protected-resource")
            specific = await client.get("/.well-known/oauth-protected-resource/mcp")
            server = await client.get("/.well-known/oauth-authorization-server")
            assert protected.status_code == 200
            assert specific.status_code == 200
            assert protected.json()["resource"] == "https://fernruf.example.com/mcp"
            assert specific.json()["authorization_servers"] == ["https://fernruf.example.com"]
            meta = server.json()
            assert meta["code_challenge_methods_supported"] == ["S256"]
            assert meta["registration_endpoint"].endswith("/register")
            assert meta["authorization_endpoint"].endswith("/authorize")
            missing = await client.get("/authorize", params={"response_type": "code", "client_id": "nope"})
            assert missing.status_code == 400
            plain = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": "nope",
                    "code_challenge": "abc",
                    "code_challenge_method": "plain",
                },
            )
            assert plain.status_code == 400
            assert "github.com" not in plain.headers.get("location", "")

    asyncio.run(run())


def test_full_flow_hashes_tokens_and_rotates_refresh(tmp_path) -> None:
    app, settings, static = _app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            issued = await _until_tokens(app)
            body = issued["body"]
            assert body["expires_in"] == 3600
            assert body["token_type"] == "Bearer"
            access = body["access_token"]
            refresh = body["refresh_token"]
            async with _client(app) as client:
                allowed = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {access}",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "m3", "version": "0"},
                        },
                    },
                )
                assert allowed.status_code != 401
                refreshed = await client.post(
                    "/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                        "client_id": issued["client_id"],
                    },
                )
                assert refreshed.status_code == 200, refreshed.text
                new_body = refreshed.json()
                assert new_body["refresh_token"] != refresh
                assert new_body["access_token"] != access
                reused = await client.post(
                    "/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                        "client_id": issued["client_id"],
                    },
                )
                assert reused.status_code == 400
                assert reused.json()["error"] == "invalid_grant"
                killed = await client.post(
                    "/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": new_body["refresh_token"],
                        "client_id": issued["client_id"],
                    },
                )
                assert killed.status_code == 400
                dead = await client.post(
                    "/mcp",
                    headers={"Authorization": f"Bearer {new_body['access_token']}"},
                )
                assert dead.status_code == 401
            return access, refresh, new_body["access_token"], new_body["refresh_token"], issued["code"]

    access, refresh, access2, refresh2, code = asyncio.run(run())
    raw = open(settings.call_settings_db, "rb").read()
    for secret in (access, refresh, access2, refresh2, code, GITHUB_TOKEN, GITHUB_SECRET, static):
        assert secret.encode() not in raw
    assert token_hash(access).encode() in raw


def test_refresh_expiry_slides_forward(tmp_path) -> None:
    app, _settings, _static = _app(tmp_path)

    async def run():
        issued = await _until_tokens(app)
        before = app.state.runtime.store.list_active_grants()[0]["refresh_expires_at"]
        async with _client(app) as client:
            await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": issued["body"]["refresh_token"],
                    "client_id": issued["client_id"],
                },
            )
        after = app.state.runtime.store.list_active_grants()[0]["refresh_expires_at"]
        assert after >= before
        assert after > time.time() + 80 * 24 * 3600

    asyncio.run(run())


def test_expired_access_token_is_rejected(tmp_path) -> None:
    app, settings, _static = _app(tmp_path)

    async def run():
        issued = await _until_tokens(app)
        access = issued["body"]["access_token"]
        import sqlite3

        conn = sqlite3.connect(settings.call_settings_db)
        conn.execute(
            "UPDATE oauth_access SET expires_at = ? WHERE token_hash = ?",
            (time.time() - 10, token_hash(access)),
        )
        conn.commit()
        conn.close()
        async with _client(app) as client:
            rejected = await client.post("/mcp", headers={"Authorization": f"Bearer {access}"})
            assert rejected.status_code == 401
            still = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": issued["body"]["refresh_token"],
                    "client_id": issued["client_id"],
                },
            )
            assert still.status_code == 200, still.text

    asyncio.run(run())


def test_other_github_user_is_denied(tmp_path) -> None:
    app, _settings, _static = _app(tmp_path, user_id=1)

    async def run():
        result = await _until_tokens(app, user_id=1)
        assert "not allowed" in result["denied"]
        assert "99999999" not in result["denied"]

    asyncio.run(run())


def test_wrong_pkce_verifier_is_rejected(tmp_path) -> None:
    app, _settings, _static = _app(tmp_path)
    verifier, challenge = _pkce()

    async def run():
        async with _client(app) as client:
            registered = await _register(client)
            client_id = registered.json()["client_id"]
            started = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": REDIRECT,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": "s",
                },
                follow_redirects=False,
            )
            txn = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
            await client.get("/oauth/github/callback", params={"code": "c", "state": txn})
            agreed = await client.post(
                "/oauth/github/callback",
                data={"txn": txn, "consent": "yes"},
                follow_redirects=False,
            )
            code = parse_qs(urlparse(agreed.headers["location"]).query)["code"][0]
            bad = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "client_id": client_id,
                    "code_verifier": verifier + "x",
                },
            )
            assert bad.status_code == 400
            assert bad.json()["error"] == "invalid_grant"

    asyncio.run(run())


def test_redirect_and_registration_limits(tmp_path) -> None:
    app, _settings, _static = _app(tmp_path, FERNRUF_OAUTH_MAX_CLIENTS=1)

    async def run():
        async with _client(app) as client:
            public = await _register(client, "https://example.com/cb")
            assert public.status_code == 201, public.text
            again = await _register(client, "https://example.com/other")
            assert again.status_code == 400
            assert "limit" in again.text
            remote = await client.post(
                "/register",
                json={
                    "redirect_uris": ["http://8.8.8.8/cb"],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                },
            )
            assert remote.status_code == 400

    asyncio.run(run())


def test_oauth_endpoints_are_rate_limited(tmp_path) -> None:
    app, _settings, _static = _app(tmp_path, FERNRUF_OAUTH_RATE_PER_MIN=2)

    async def run():
        async with _client(app) as client:
            codes = []
            for _ in range(3):
                response = await client.post("/register", content=b"{}")
                codes.append(response.status_code)
            return codes

    assert asyncio.run(run()) == [400, 400, 429]


def test_static_token_can_be_left_unset(tmp_path) -> None:
    app, _settings, static = _app(tmp_path, token="")
    assert static == ""

    async def run():
        issued = await _until_tokens(app)
        async with app.router.lifespan_context(app):
            async with _client(app) as client:
                missing = await client.post("/mcp")
                assert missing.status_code == 401
                used = await client.post(
                    "/mcp",
                    headers={"Authorization": f"Bearer {issued['body']['access_token']}"},
                )
                assert used.status_code != 401

    asyncio.run(run())


def test_startup_requires_some_login(tmp_path) -> None:
    settings, _token = service_settings(tmp_path, token="")
    with pytest_raises():
        create_app(settings)


def pytest_raises():
    import pytest

    return pytest.raises(AuthConfigError)


def test_auth_cli_lists_and_revokes(tmp_path) -> None:
    app, settings, _static = _app(tmp_path)
    asyncio.run(_until_tokens(app))
    env = {
        "FERNRUF_ENV_FILE": "none",
        "FERNRUF_SETTINGS_DB": settings.call_settings_db,
        "FERNRUF_MCP_TOKEN": fresh_token(),
    }
    listed = CliRunner().invoke(main, ["auth", "list"], env=env)
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert rows[0]["github_user_id"] == "99999999"
    assert rows[0]["client_name"] == "Example MCP Client"
    assert "access_token" not in listed.output
    assert GITHUB_TOKEN not in listed.output
    revoked = CliRunner().invoke(main, ["auth", "revoke-all"], env=env)
    assert revoked.output.strip() == "revoked 1"
    empty = CliRunner().invoke(main, ["auth", "list"], env=env)
    assert json.loads(empty.output) == []


def test_serve_refuses_when_nothing_can_log_in(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        ["serve"],
        env={
            "FERNRUF_ENV_FILE": "none",
            "FERNRUF_MCP_TOKEN": "",
            "FERNRUF_GITHUB_CLIENT_ID": "",
            "FERNRUF_GITHUB_CLIENT_SECRET": "",
            "FERNRUF_SETTINGS_DB": str(tmp_path / "c.sqlite"),
        },
    )
    assert result.exit_code == 1
    assert "FERNRUF_MCP_TOKEN" in result.output + (result.stderr or "")
