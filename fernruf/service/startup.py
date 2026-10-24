"""Uvicorn server that announces itself only after the socket is bound."""

from __future__ import annotations

import logging

import uvicorn

from fernruf.privacy_notice import log_privacy_warnings
from fernruf.service.notifier import announce_service_started
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.startup")


class FernrufServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, settings: Settings, *, github: bool, static_token: bool) -> None:
        super().__init__(config)
        self._settings = settings
        self._github = github
        self._static_token = static_token

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if self.should_exit:
            return
        log_privacy_warnings(self._settings)
        try:
            announce_service_started(
                self._settings,
                github=self._github,
                static_token=self._static_token,
            )
        except Exception:
            LOG.warning("fernruf startup notice failed")
