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
    assert "fernruf.example.com" in hosts
    assert "fernruf.example.com:*" in hosts
    assert "alt.example.com" in hosts
    assert "127.0.0.1" in hosts
