from __future__ import annotations

from fernruf.service.mcp_hosts import mcp_allowed_hosts
from fernruf.settings import Settings


def test_mcp_allowed_hosts_from_oauth_urls() -> None:
    settings = Settings(
        _env_file=None,
        oauth_issuer="https://fernruf.example.com",
        oauth_resource="https://fernruf.example.com/mcp",
        allowed_mcp_hosts="alt.example.com",
    )
    hosts = mcp_allowed_hosts(settings)
    assert hosts == [
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "alt.example.com",
        "alt.example.com:*",
        "fernruf.example.com",
        "fernruf.example.com:*",
        "localhost",
        "localhost:*",
    ]
