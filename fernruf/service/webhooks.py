"""Webhook URL checks. The signed call.finalized delivery is M4; M3 stores the URL."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)


def _public_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def webhook_url_error(url: str) -> str | None:
    """Return an error message when the URL must not be stored, else None."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return "Webhook URL must be https."
    if parsed.username or parsed.password:
        return "Webhook URL must not contain credentials."
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return "Webhook URL has no host."
    if host in _BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        return "Webhook URL must not target a local or metadata host."
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    if not _public_ip(host):
        return "Webhook URL must not target a private address."
    return None
