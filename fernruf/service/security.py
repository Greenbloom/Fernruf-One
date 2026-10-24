"""Bearer auth, bind address, and failed-auth lockout for the M3 service.

The public URL is only a door for your MCP client. A missing, short, or
wrong token never reaches a tool.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import defaultdict
from collections.abc import Callable

from starlette.requests import Request

DEFAULT_BIND_HOST = "127.0.0.1"
WG_BIND_HOST = DEFAULT_BIND_HOST
DEFAULT_BIND_PORT = 8787
TRUSTED_PROXY = "127.0.0.1"
AUTH_LOG_LINE = "fernruf auth failed ip=%s reason=%s"

_PLACEHOLDER_SNIPPETS = (
    "change-me",
    "changeme",
    "placeholder",
    "example-token",
    "your-token",
    "insert-token",
    "replace-me",
    "fernruf-dev",
    "password",
    "todo",
    "fixme",
)
_HEX = re.compile(r"^[0-9a-fA-F]+$")


class McpTokenError(ValueError):
    """FERNRUF_MCP_TOKEN is missing, short, or a placeholder. The value is not included."""


class BindError(ValueError):
    """Refusing to listen on this address."""


def generate_mcp_token() -> str:
    """32 random bytes, url-safe, suitable for FERNRUF_MCP_TOKEN."""
    return secrets.token_urlsafe(32)


def token_byte_length(token: str) -> int:
    """Decoded length. Hex is counted as bytes; otherwise url-safe base64."""
    if _HEX.fullmatch(token) and len(token) % 2 == 0:
        return len(token) // 2
    pad = "=" * ((4 - len(token) % 4) % 4)
    try:
        # urlsafe_b64decode does not take validate=; a second argument raises
        # TypeError and used to make every generated token look empty.
        raw = base64.urlsafe_b64decode(token + pad)
    except Exception:
        return 0
    return len(raw)


def validate_mcp_token(token: str | None) -> str:
    """Return the token or raise McpTokenError. Never echoes the token."""
    if token is None or token == "":
        raise McpTokenError(
            "FERNRUF_MCP_TOKEN is not set. Generate one with: fernruf gen-token"
        )
    if token != token.strip() or any(ch.isspace() for ch in token):
        raise McpTokenError("FERNRUF_MCP_TOKEN must be a single token with no whitespace")
    lowered = token.casefold()
    for snippet in _PLACEHOLDER_SNIPPETS:
        if snippet in lowered:
            raise McpTokenError(
                "FERNRUF_MCP_TOKEN is a placeholder. Generate one with: fernruf gen-token"
            )
    if token_byte_length(token) < 32:
        raise McpTokenError(
            "FERNRUF_MCP_TOKEN must be at least 32 random bytes. "
            "Generate one with: fernruf gen-token"
        )
    if len(set(token)) < 8:
        raise McpTokenError(
            "FERNRUF_MCP_TOKEN is not random enough. Generate one with: fernruf gen-token"
        )
    return token


def bearer_matches(presented: str, expected: str) -> bool:
    """Constant-time compare. Both sides are hashed so length does not leak."""
    left = hashlib.sha256(presented.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def validate_bind_host(host: str, *, override: str | None) -> str:
    """Allow loopback by default; other addresses need an explicit override match."""
    cleaned = (host or "").strip()
    if not cleaned:
        raise BindError("FERNRUF_BIND_HOST is empty")
    if cleaned in ("127.0.0.1", "::1", "localhost"):
        return cleaned
    allowed = (override or "").strip()
    if allowed and allowed == cleaned:
        return cleaned
    raise BindError(
        f"refusing to bind {cleaned}. Default is 127.0.0.1. "
        "Set FERNRUF_BIND_OVERRIDE to that exact host to listen elsewhere. "
        "Do not bind 0.0.0.0 without understanding the exposure."
    )


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """Client address for auth logs.

    X-Forwarded-For is honored only when the TCP peer is the VPS tunnel
    (127.0.0.1), because nginx sets that header to ``$remote_addr``. Any other
    peer's header is ignored.
    """
    peer = request.client.host if request.client else ""
    if peer not in trusted_proxies:
        return peer or "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not parts:
        return peer or "unknown"
    # One address when nginx uses $remote_addr. The last entry is the one a
    # trusted proxy added if the header was appended instead.
    candidate = parts[-1]
    if _is_ip(candidate):
        return candidate
    return peer or "unknown"


class AuthLockout:
    """Per-IP failure window. Locked clients get 429 until the lock expires."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_s: float = 300,
        lockout_s: float = 900,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._now = now or time.monotonic
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._locked_until: dict[str, float] = {}

    def locked(self, ip: str) -> bool:
        until = self._locked_until.get(ip, 0.0)
        return until > self._now()

    def retry_after_s(self, ip: str) -> int:
        remaining = self._locked_until.get(ip, 0.0) - self._now()
        return max(1, int(remaining + 0.999))

    def record_failure(self, ip: str) -> None:
        now = self._now()
        recent = [stamp for stamp in self._failures[ip] if now - stamp < self.window_s]
        recent.append(now)
        self._failures[ip] = recent
        if len(recent) >= self.max_failures:
            self._locked_until[ip] = now + self.lockout_s
            self._failures[ip] = []

    def record_success(self, ip: str) -> None:
        if self.locked(ip):
            return
        self._failures.pop(ip, None)
        self._locked_until.pop(ip, None)


class WindowLimiter:
    """Fixed window for the OAuth endpoints. Separate from the bearer lockout."""

    def __init__(self, max_hits: int, window_s: float = 60.0) -> None:
        self.max_hits = max_hits
        self.window_s = window_s
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        recent = [stamp for stamp in self._hits[key] if now - stamp < self.window_s]
        if len(recent) >= self.max_hits:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        return True


class AuthConfigError(ValueError):
    """Neither a static token nor GitHub OAuth is configured, or only half of GitHub is set."""


def configured_static_token(token: str | None) -> str | None:
    """Empty means the static bearer is off. A non-empty value must still be strong."""
    if token is None or token == "":
        return None
    return validate_mcp_token(token)


def github_login_configured(client_id: str, client_secret: str) -> bool:
    return bool(client_id.strip() and client_secret.strip())


def require_login_configured(token: str | None, client_id: str, client_secret: str) -> str | None:
    static = configured_static_token(token)
    has_id = bool(client_id.strip())
    has_secret = bool(client_secret.strip())
    if has_id != has_secret:
        raise AuthConfigError(
            "Set both FERNRUF_GITHUB_CLIENT_ID and FERNRUF_GITHUB_CLIENT_SECRET, or neither."
        )
    if static is None and not has_id:
        raise AuthConfigError(
            "Set FERNRUF_MCP_TOKEN or GitHub OAuth credentials. "
            "Generate a token with: fernruf gen-token"
        )
    return static
