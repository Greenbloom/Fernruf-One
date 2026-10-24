"""Whether Asterisk's SIP trunk endpoint is registered. No SIP is placed here."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.line")


class LineHealth:
    def ready(self) -> bool:
        raise NotImplementedError


class StaticLineHealth(LineHealth):
    def __init__(self, ready: bool) -> None:
        self._ready = ready

    def ready(self) -> bool:
        return self._ready


class AriLineHealth(LineHealth):
    """ARI endpoint state. ``online`` means the trunk has a reachable contact."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def ready(self) -> bool:
        url = (
            self._settings.asterisk_ari_url.rstrip("/")
            + "/ari/endpoints/PJSIP/sip-trunk"
        )
        own = self._client is None
        client = self._client or httpx.Client(timeout=3.0)
        try:
            response = client.get(
                url,
                auth=(self._settings.asterisk_ari_user, self._settings.asterisk_ari_password),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            LOG.info("line health: ARI unreachable")
            return False
        finally:
            if own:
                client.close()
        if response.status_code != 200:
            LOG.info("line health: ARI HTTP %s", response.status_code)
            return False
        try:
            state = str(response.json().get("state", "")).lower()
        except ValueError:
            return False
        return state == "online"


def line_health_for(settings: Settings) -> LineHealth:
    return AriLineHealth(settings)


Probe = Callable[[], bool]
