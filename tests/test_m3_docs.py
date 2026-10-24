"""Deploy examples and server docs match the service bind and security notes."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_nginx_snippet_matches_the_live_proxy() -> None:
    nginx = _text("deploy", "examples", "nginx-fernruf.conf")
    doc = _text("docs", "SERVER.md")
    assert "fernruf.example.com" in doc
    assert "http://127.0.0.1:8787" in doc
    for blob in (nginx,):
        assert "fernruf.example.com" in blob
        assert "http://127.0.0.1:8787" in blob
        assert "X-Forwarded-For $remote_addr" in blob
        assert "proxy_buffering off" in blob
        assert "proxy_read_timeout" in blob
        assert "limit_req" in blob
        assert "30r/m" in blob
        assert "client_max_body_size" in blob
        assert "return 404" in blob
        assert "/mcp" in blob
        assert "/healthz" in blob
        assert "letsencrypt" in blob or "certbot" in blob
    assert "127.0.0.1:8080" not in nginx
    assert "other services" in nginx
    assert "ssl_certificate" in nginx
    assert "/etc/letsencrypt/" in nginx


def test_systemd_binds_loopback_only() -> None:
    unit = _text("deploy", "examples", "fernruf.service")
    assert "127.0.0.1" in unit
    assert "8787" in unit
    assert "FERNRUF_BIND_HOST=127.0.0.1" in unit
    assert "FERNRUF_BIND_PORT=8787" in unit
    assert "uv run fernruf serve" in unit
    assert "0.0.0.0" not in unit


def test_fail2ban_matches_nginx_401_and_the_app_line() -> None:
    nginx_filter = _text("deploy", "examples", "fail2ban", "filter.d", "fernruf-nginx.conf")
    journal_filter = _text("deploy", "examples", "fail2ban", "filter.d", "fernruf-journal.conf")
    jail = _text("deploy", "examples", "fail2ban", "jail.d", "fernruf.conf")
    doc = _text("docs", "SERVER.md")
    assert 'failregex = ^<HOST> ".*" 401$' in nginx_filter
    assert "fernruf auth failed ip=<HOST> reason=\\S+" in journal_filter
    assert "fernruf.access.log" in jail
    assert "maxretry = 5" in jail
    assert "findtime = 300" in jail
    assert "bantime = 900" in jail
    assert "fail2ban" in doc
    assert "127.0.0.1" in doc


def test_docs_name_the_token_and_the_cli_allowlist() -> None:
    doc = _text("docs", "SERVER.md")
    example = _text(".env.example")
    arch = _text("ARCHITECTURE.md")
    for blob in (doc, example):
        assert "FERNRUF_MCP_TOKEN" in blob
        assert "FERNRUF_BIND_PORT" in blob
        assert "8787" in blob
    assert "fernruf gen-token" in doc
    assert "settings allow-number" in doc
    assert "setting_requires_cli" in doc
    assert "127.0.0.1" in arch and "8787" in arch
    compose = _text("docker-compose.yml")
    assert "8787" in compose
    assert "127.0.0.1" in compose


def test_docs_list_oauth_paths_and_notify() -> None:
    doc = _text("docs", "SERVER.md")
    assert "oauth-protected-resource" in doc
    assert "FERNRUF_NOTIFY" in doc or "notify" in doc.lower()
