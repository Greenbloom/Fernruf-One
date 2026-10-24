"""Auth, bind address, lockout, and the public HTTP surface."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.service.mcp_app import create_app
from fernruf.service.security import (
    AUTH_LOG_LINE,
    AuthLockout,
    BindError,
    McpTokenError,
    WG_BIND_HOST,
    bearer_matches,
    generate_mcp_token,
    validate_bind_host,
    validate_mcp_token,
)
from fernruf.settings import Settings
from tests.m3_support import fresh_token, service_settings


def test_token_must_be_32_bytes_and_not_a_placeholder() -> None:
    generated = generate_mcp_token()
    assert validate_mcp_token(generated) == generated
    with pytest.raises(McpTokenError):
        validate_mcp_token("")
    with pytest.raises(McpTokenError):
        validate_mcp_token(None)
    with pytest.raises(McpTokenError):
        validate_mcp_token("short-token")
    with pytest.raises(McpTokenError):
        validate_mcp_token("change-me-" + "a" * 40)
    with pytest.raises(McpTokenError):
        validate_mcp_token("a" * 64)


def test_gen_token_prints_a_usable_token() -> None:
    result = CliRunner().invoke(main, ["gen-token"], env={"FERNRUF_ENV_FILE": "none"})
    assert result.exit_code == 0
    token = result.output.strip()
    assert "\n" not in token
    validate_mcp_token(token)


def test_bearer_compare_is_exact() -> None:
    token = fresh_token()
    assert bearer_matches(token, token)
    assert not bearer_matches(token, token + "x")
    assert not bearer_matches("short", token)
    assert not bearer_matches("", token)


def test_bind_defaults_and_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FERNRUF_BIND_HOST", raising=False)
    monkeypatch.delenv("FERNRUF_BIND_PORT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 8787
    assert validate_bind_host(WG_BIND_HOST, override="") == WG_BIND_HOST
    assert validate_bind_host(WG_BIND_HOST, override=None) == WG_BIND_HOST
    for host in ("0.0.0.0", "::", "8.8.8.8", "192.0.2.22"):
        with pytest.raises(BindError):
            validate_bind_host(host, override="")
        with pytest.raises(BindError):
            validate_bind_host(host, override="1")
        with pytest.raises(BindError):
            validate_bind_host(host, override="true")
    assert validate_bind_host("127.0.0.1", override="127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("0.0.0.0", override="0.0.0.0") == "0.0.0.0"


def test_serve_refuses_short_token_without_echoing_it(tmp_path) -> None:
    secret = "short-but-distinct-value"
    result = CliRunner().invoke(
        main,
        ["serve"],
        env={
            "FERNRUF_ENV_FILE": "none",
            "FERNRUF_MCP_TOKEN": secret,
            "FERNRUF_SETTINGS_DB": str(tmp_path / "c.sqlite"),
            "FERNRUF_BIND_HOST": "0.0.0.0",
        },
    )
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert secret not in combined
    assert "Authorization" not in combined


def test_serve_refuses_public_bind(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        ["serve"],
        env={
            "FERNRUF_ENV_FILE": "none",
            "FERNRUF_MCP_TOKEN": fresh_token(),
            "FERNRUF_SETTINGS_DB": str(tmp_path / "c.sqlite"),
            "FERNRUF_BIND_HOST": "0.0.0.0",
            "FERNRUF_BIND_OVERRIDE": "",
        },
    )
    assert result.exit_code == 1
    assert "refusing to bind" in (result.output + (result.stderr or ""))


def test_create_app_refuses_weak_token(tmp_path) -> None:
    settings, _token = service_settings(tmp_path, token="nope")
    with pytest.raises(McpTokenError):
        create_app(settings)


def _client(app, host: str = "203.0.113.10"):
    transport = httpx.ASGITransport(app=app, client=(host, 1234))
    return httpx.AsyncClient(transport=transport, base_url="http://fernruf.example.com")


def test_healthz_is_only_ok_and_other_routes_are_closed(tmp_path, caplog) -> None:
    settings, token = service_settings(tmp_path)
    app = create_app(settings)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None

    async def run() -> None:
        async with _client(app) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"ok": True}
            assert set(health.json()) == {"ok"}
            for path in ("/docs", "/redoc", "/openapi.json", "/favicon.ico", "/admin"):
                missed = await client.get(path)
                assert missed.status_code == 404
                assert missed.json() == {"error": "not_found"}
            bare = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            assert bare.status_code == 401
            assert bare.json() == {"error": "unauthorized"}
            assert "www-authenticate" in {k.lower() for k in bare.headers}
            assert (
                'resource_metadata="https://fernruf.example.com/.well-known/oauth-protected-resource/mcp"'
                in bare.headers["www-authenticate"]
            )
            wrong = await client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {token}nope"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert wrong.status_code == 401

    caplog.set_level(logging.WARNING, logger="fernruf.auth")
    asyncio.run(run())
    text = caplog.text
    assert token not in text
    assert "Bearer" not in text
    assert "Authorization" not in text
    assert "fernruf auth failed ip=203.0.113.10 reason=missing_bearer" in text
    assert "fernruf auth failed ip=203.0.113.10 reason=bad_token" in text


def test_forwarded_for_trusted_only_from_tunnel_peer(tmp_path, caplog) -> None:
    settings, _token = service_settings(tmp_path)
    app = create_app(settings)
    caplog.set_level(logging.WARNING, logger="fernruf.auth")

    async def run() -> None:
        async with _client(app, "127.0.0.1") as trusted:
            await trusted.post(
                "/mcp",
                headers={"X-Forwarded-For": "203.0.113.50", "Authorization": "Bearer nope"},
            )
        async with _client(app, "198.51.100.4") as stranger:
            await stranger.post(
                "/mcp",
                headers={"X-Forwarded-For": "203.0.113.50", "Authorization": "Bearer nope"},
            )
        async with _client(app, "127.0.0.1") as trusted_chain:
            await trusted_chain.post(
                "/mcp",
                headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.77"},
            )

    asyncio.run(run())
    assert "ip=203.0.113.50" in caplog.text
    assert "ip=198.51.100.4" in caplog.text
    assert "ip=1.2.3.4" not in caplog.text
    assert "ip=203.0.113.77" in caplog.text


def test_lockout_returns_429_then_expires(tmp_path) -> None:
    settings, token = service_settings(tmp_path)
    clock = {"t": 0.0}
    lock = AuthLockout(max_failures=3, window_s=100, lockout_s=30, now=lambda: clock["t"])
    app = create_app(settings, lockout=lock)

    async def run() -> list[int]:
        codes = []
        async with _client(app, "203.0.113.60") as client:
            for _ in range(3):
                response = await client.post("/mcp", headers={"Authorization": "Bearer wrong"})
                codes.append(response.status_code)
            locked = await client.post("/mcp", headers={"Authorization": f"Bearer {token}"})
            codes.append(locked.status_code)
            assert locked.json() == {"error": "rate_limited"}
            assert locked.headers["retry-after"]
            clock["t"] = 31
            again = await client.post("/mcp")
            codes.append(again.status_code)
        return codes

    assert asyncio.run(run()) == [401, 401, 401, 429, 401]


def test_authenticated_mcp_initialize_is_not_rejected(tmp_path) -> None:
    settings, token = service_settings(tmp_path)
    app = create_app(settings)

    async def run() -> int:
        async with app.router.lifespan_context(app):
            async with _client(app) as client:
                response = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {token}",
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
                            "clientInfo": {"name": "m3-test", "version": "0"},
                        },
                    },
                )
                return response.status_code

    assert asyncio.run(run()) != 401


def test_oversized_mcp_body_is_rejected_when_authenticated(tmp_path) -> None:
    settings, token = service_settings(tmp_path)
    app = create_app(settings)

    async def run() -> int:
        async with app.router.lifespan_context(app):
            async with _client(app) as client:
                response = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    content=b"{" + b"x" * (70 * 1024),
                )
                return response.status_code

    assert asyncio.run(run()) == 413


def test_auth_log_format_is_stable() -> None:
    assert AUTH_LOG_LINE == "fernruf auth failed ip=%s reason=%s"
