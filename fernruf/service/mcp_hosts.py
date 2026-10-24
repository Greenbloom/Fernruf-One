"""Hosts allowed for MCP streamable HTTP (DNS rebinding protection)."""

from __future__ import annotations

from urllib.parse import urlparse

from fernruf.settings import Settings


def mcp_allowed_hosts(settings: Settings) -> list[str]:
    """Derive from OAuth URLs, optional FERNRUF_ALLOWED_HOSTS, and loopback defaults."""
    hosts: set[str] = {
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    }
    for raw in (settings.oauth_resource, settings.oauth_issuer, settings.github_callback_url):
        text = (raw or "").strip()
        if not text:
            continue
        parsed = urlparse(text)
        if parsed.hostname:
            hosts.add(parsed.hostname)
            if parsed.port:
                hosts.add(f"{parsed.hostname}:{parsed.port}")
            else:
                hosts.add(f"{parsed.hostname}:*")
    extra = (settings.allowed_mcp_hosts or "").strip()
    for part in extra.replace(";", ",").split(","):
        name = part.strip()
        if name:
            hosts.add(name)
            if ":" not in name:
                hosts.add(f"{name}:*")
    return sorted(hosts)
