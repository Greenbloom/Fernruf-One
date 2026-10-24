from __future__ import annotations

import logging
import re
from typing import Iterable

_BEARER_RE = re.compile(r"Bearer\s+\S+", re.I)
_API_KEY_RE = re.compile(r"api_key=[^&\s\"']+", re.I)
_AUTH_BEARER_RE = re.compile(r"Authorization:\s*Bearer\s+\S+", re.I)

_NOISY_LOGGERS = ("httpx", "httpcore", "websockets", "urllib3", "hpack")


class RedactSecretsFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = _AUTH_BEARER_RE.sub("Authorization: [REDACTED]", msg)
        redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
        redacted = _API_KEY_RE.sub("api_key=[REDACTED]", redacted)
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED]")
        record.msg = redacted
        record.args = ()
        record.message = redacted
        return True


def collect_secret_literals(settings) -> tuple[str, ...]:
    parts: list[str] = []
    for val in (
        settings.fish_audio_api_key,
        settings.llm_api_key_resolved,
        settings.llm_api_key,
        settings.openrouter_api_key,
        settings.asterisk_ari_password,
        getattr(settings, "sip_password", ""),
        getattr(settings, "typesafe_api_key", ""),
        getattr(settings, "mcp_token", ""),
        getattr(settings, "webhook_secret", ""),
        getattr(settings, "github_client_secret", ""),
    ):
        if val:
            parts.append(val)
    return tuple(parts)


def configure_logging(verbose: bool, *, secrets: Iterable[str] = ()) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s", force=True)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    secret_tuple = tuple(secrets)
    if secret_tuple:
        filt = RedactSecretsFilter(secret_tuple)
        root = logging.getLogger()
        root.addFilter(filt)
        for hdlr in root.handlers:
            hdlr.addFilter(filt)
        for name in _NOISY_LOGGERS:
            lg = logging.getLogger(name)
            lg.addFilter(filt)
            for hdlr in lg.handlers:
                hdlr.addFilter(filt)
