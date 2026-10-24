from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import websockets

from fernruf.settings import Settings
from fernruf.tts.fish import TTS_SAMPLE_RATE

LOG = logging.getLogger("fernruf.tts.fish_ws")

_DEFAULT_WS_URL = "wss://api.fish.audio/v1/tts/live"


@dataclass
class FishLiveWsSession:
    """One Fish live WebSocket TTS session per call (JSON events; REST fallback on failure)."""

    settings: Settings
    api_key: str
    model: str
    voice_id: str | None
    ws_url: str = _DEFAULT_WS_URL
    fallback_rest: bool = False
    _ws: Any = None
    _recv_task: asyncio.Task | None = None
    _audio_queue: asyncio.Queue[tuple[bytes, float | None] | None] = field(
        default_factory=asyncio.Queue
    )
    _pending_pcm: list[bytes] = field(default_factory=list)
    _closed: bool = False

    async def connect(self) -> None:
        if self.fallback_rest or self._ws is not None:
            return
        headers = {"Authorization": f"Bearer {self.api_key}", "model": self.model}
        try:
            self._ws = await websockets.connect(self.ws_url, additional_headers=headers)
        except Exception as exc:
            LOG.warning("Fish WebSocket connect failed (%s); REST fallback for call", exc)
            self.fallback_rest = True
            return
        start: dict[str, Any] = {
            "event": "start",
            "request": {
                "text": "",
                "latency": self.settings.fish_latency,
                "format": "pcm",
                "sample_rate": TTS_SAMPLE_RATE,
                "chunk_length": self.settings.fish_chunk_length,
                "model": self.model,
            },
        }
        if self.voice_id:
            start["request"]["reference_id"] = self.voice_id
        await self._ws.send(json.dumps(start))
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        t0 = time.monotonic()
        first = True
        try:
            while not self._closed:
                msg = await self._ws.recv()
                if isinstance(msg, bytes):
                    ttfa = (time.monotonic() - t0) * 1000 if first else None
                    first = False
                    await self._audio_queue.put((msg, ttfa))
                    continue
                data = json.loads(msg)
                if data.get("event") == "error":
                    LOG.warning("Fish WebSocket error: %s", str(data)[:200])
                    self.fallback_rest = True
                    await self._audio_queue.put(None)
                    break
                if data.get("event") in ("finish", "stop"):
                    await self._audio_queue.put(None)
                    break
        except Exception as exc:
            LOG.warning("Fish WebSocket recv failed (%s); REST fallback", exc)
            self.fallback_rest = True
            await self._audio_queue.put(None)

    async def send_text(self, text: str) -> None:
        if self.fallback_rest or not self._ws:
            return
        await self._ws.send(json.dumps({"event": "text", "text": text}))

    async def flush(self) -> None:
        if self.fallback_rest or not self._ws:
            return
        await self._ws.send(json.dumps({"event": "flush"}))

    async def stream_utterance(self, text: str) -> AsyncIterator[tuple[bytes, float | None]]:
        if self.fallback_rest:
            return
        await self.send_text(text)
        await self.flush()
        while True:
            item = await self._audio_queue.get()
            if item is None:
                break
            yield item

    async def cancel(self) -> None:
        if self.fallback_rest:
            return
        if self._ws:
            try:
                await self._ws.send(json.dumps({"event": "stop"}))
            except Exception:
                pass
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self.connect()

    async def close(self) -> None:
        self._closed = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
        if self._ws:
            try:
                await self._ws.send(json.dumps({"event": "stop"}))
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
