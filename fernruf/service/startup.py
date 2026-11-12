"""Uvicorn server that announces itself only after the socket is bound."""

from __future__ import annotations

import contextlib
import logging

import uvicorn

from fernruf.privacy_notice import log_privacy_warnings
from fernruf.service.notifier import announce_service_started
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.startup")


class FernrufServer(uvicorn.Server):
    def __init__(
        self,
        config: uvicorn.Config,
        settings: Settings,
        *,
        github: bool,
        static_token: bool,
        monitor_token: str = "",
    ) -> None:
        super().__init__(config)
        self._settings = settings
        self._github = github
        self._static_token = static_token
        self._monitor_token = monitor_token
        self._monitor_server = None

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
        await self._start_monitor()

    async def _start_monitor(self) -> None:
        token = self._monitor_token
        if not token or not self._settings.monitor_enabled:
            return
        app = self.config.app
        monitor = getattr(getattr(app, "state", None), "monitor", None)
        if monitor is None:
            return
        from fernruf.monitor.server import start_monitor_server

        try:
            self._monitor_server = await start_monitor_server(
                monitor,
                token,
                port=int(self._settings.monitor_port),
            )
        except Exception:
            LOG.warning("local call monitor did not start")
            return
        LOG.info("local call monitor on 127.0.0.1:%s", self._monitor_server.port)

    async def shutdown(self, sockets=None) -> None:
        server = self._monitor_server
        self._monitor_server = None
        if server is not None:
            with contextlib.suppress(Exception):
                await server.close()
        await super().shutdown(sockets)
