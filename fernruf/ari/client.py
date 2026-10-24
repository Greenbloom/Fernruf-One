from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import websockets

LOG = logging.getLogger("fernruf.ari.client")


@dataclass
class AriSession:
    base_url: str
    user: str
    password: str
    app: str = "fernruf"
    _client: httpx.Client = field(init=False, repr=False)
    active_channel_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/") + "/ari",
            auth=(self.user, self.password),
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def hangup(self, channel_id: str) -> None:
        path = f"/channels/{quote(channel_id)}"
        r = self._client.delete(path)
        LOG.info("ARI DELETE %s -> %s", path, r.status_code)
        if r.status_code not in (204, 404):
            r.raise_for_status()

    def destroy_bridge(self, bridge_id: str) -> None:
        path = f"/bridges/{quote(bridge_id)}"
        r = self._client.delete(path)
        LOG.info("ARI DELETE %s -> %s", path, r.status_code)
        if r.status_code not in (204, 404):
            r.raise_for_status()

    def hangup_active(self) -> None:
        if self.active_channel_id:
            with contextlib.suppress(Exception):
                self.hangup(self.active_channel_id)
            self.active_channel_id = None

    def play_sound(self, channel_id: str, media: str) -> None:
        r = self._client.post(f"/channels/{quote(channel_id)}/play", params={"media": media})
        r.raise_for_status()

    def subscribe_channel(self, channel_id: str) -> None:
        """Subscribe the Stasis app to channel events (incl. DTMF) before answer."""
        r = self._client.post(
            f"/applications/{quote(self.app)}/subscription",
            params={"eventSource": f"channel:{channel_id}"},
        )
        if r.status_code not in (200, 204, 409):
            r.raise_for_status()

    async def run_stasis_call(
        self,
        endpoint: str,
        *,
        caller_id: str,
        play_media: str,
        min_hold_s: float = 32.0,
        answer_timeout_s: float = 90.0,
    ) -> dict[str, Any]:
        ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/ari/events?api_key={quote(self.user)}:{quote(self.password)}&app={self.app}"
        )
        dtmf: list[str] = []
        start = time.monotonic()
        answered_at: float | None = None
        channel_id: str | None = None
        last_state: str | None = None
        last_progress_log = 0.0
        channel_up = asyncio.Event()
        done = asyncio.Event()

        async with websockets.connect(ws_url) as ws:
            await asyncio.sleep(0.3)

            tracked_id: str | None = None

            async def reader() -> None:
                nonlocal channel_id, tracked_id, answered_at, last_state, last_progress_log
                try:
                    while not done.is_set():
                        try:
                            raw = await ws.recv()
                        except websockets.ConnectionClosed:
                            if done.is_set():
                                return
                            raise
                        ev: dict[str, Any] = json.loads(raw)
                        et = ev.get("type")
                        ch = ev.get("channel") or {}
                        cid = ch.get("id")
                        active = tracked_id or channel_id
                        if active and cid and cid != active:
                            continue

                        if et == "StasisStart" and tracked_id is None and cid:
                            tracked_id = cid
                            channel_id = cid
                            self.active_channel_id = channel_id
                        elif et == "ChannelStateChange" and active and cid == active:
                            state = ch.get("state", "")
                            if state != last_state:
                                last_state = state
                                if state in ("Ring", "Ringing", "Up", "Down"):
                                    LOG.info("channel state: %s", state)
                            if state == "Up" and answered_at is None:
                                answered_at = time.monotonic()
                                self.play_sound(channel_id, play_media)
                                channel_up.set()
                        elif et == "ChannelDtmfReceived" and active and cid == active:
                            dtmf.append(str(ev.get("digit", "")))
                        elif et in ("StasisEnd", "ChannelDestroyed") and active and cid == active:
                            done.set()
                            break
                except asyncio.CancelledError:
                    return

            read_task = asyncio.create_task(reader())

            r = self._client.post(
                "/channels",
                params={
                    "endpoint": endpoint,
                    "app": self.app,
                    "appArgs": "m1",
                    "callerId": caller_id,
                },
            )
            if r.status_code >= 400:
                done.set()
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, websockets.ConnectionClosed):
                    await read_task
                detail = (r.text or "").strip().replace("\n", " ")[:300]
                LOG.error("ARI originate failed: %s %s", r.status_code, detail)
                raise httpx.HTTPStatusError(
                    f"ARI originate failed: {r.status_code} {detail}",
                    request=r.request,
                    response=r,
                ) from None
            channel_id = r.json()["id"]
            tracked_id = channel_id
            self.active_channel_id = channel_id
            self.subscribe_channel(channel_id)
            ring_start = time.monotonic()

            try:
                while not channel_up.is_set() and not done.is_set():
                    elapsed = time.monotonic() - ring_start
                    if elapsed > answer_timeout_s:
                        break
                    if time.monotonic() - last_progress_log >= 12.0:
                        LOG.info("ringing… (%.0f s)", elapsed)
                        last_progress_log = time.monotonic()
                    await asyncio.sleep(1.0)

                if channel_up.is_set() and answered_at is not None:
                    while time.monotonic() - answered_at < min_hold_s and not done.is_set():
                        await asyncio.sleep(1.0)
                    if channel_id:
                        self.hangup(channel_id)
            finally:
                done.set()
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read_task
                self.active_channel_id = None

        hold = (time.monotonic() - answered_at) if answered_at else 0.0
        return {
            "channel_id": channel_id,
            "answered": answered_at is not None,
            "hold_s": hold,
            "dtmf": dtmf,
            "duration_s": time.monotonic() - start,
        }
