from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from fernruf.settings import Settings
from fernruf.tts.fish_rest_client import astream_fish_tts_pcm_rest


class _ChunkedFishHandler(BaseHTTPRequestHandler):
    pcm: bytes = b"\x01\x02" * 400
    delay_s: float = 0.12
    inter_chunk_delay_s: float = 0.04
    chunk_size: int = 37  # odd size to test sample alignment

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        assert body.get("latency") == "low"
        assert body.get("chunk_length") == 100
        self.send_response(200)
        self.send_header("Content-Type", "audio/pcm")
        self.end_headers()
        time.sleep(self.delay_s)
        data = self.pcm
        for i in range(0, len(data), self.chunk_size):
            if i:
                time.sleep(self.inter_chunk_delay_s)
            self.wfile.write(data[i : i + self.chunk_size])
            self.wfile.flush()


def _start_chunk_server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _ChunkedFishHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}"


def test_fish_rest_streams_chunks_with_early_ttfa() -> None:
    server, base = _start_chunk_server()
    settings = Settings(
        _env_file=None,
        fish_latency="low",
        fish_chunk_length=100,
        fish_audio_api_key="test-key",
        fish_audio_model="s2.1-pro-free",
    )
    client = httpx.AsyncClient(base_url=base, timeout=30.0)

    async def run() -> tuple[bytes, float | None, float, int]:
        t0 = time.monotonic()
        chunks: list[bytes] = []
        ttfa: float | None = None
        async for pcm, first_ttfa in astream_fish_tts_pcm_rest(
            "Hallo.",
            settings=settings,
            api_key="test-key",
            model="s2.1-pro-free",
            voice_id=None,
            client=client,
            tts_url="/v1/tts",
        ):
            if first_ttfa is not None:
                ttfa = first_ttfa
            chunks.append(pcm)
        elapsed = time.monotonic() - t0
        return b"".join(chunks), ttfa, elapsed, len(chunks)

    try:
        pcm, ttfa, elapsed, n_chunks = asyncio.run(run())
    finally:
        asyncio.run(client.aclose())
        server.shutdown()

    assert len(pcm) == len(_ChunkedFishHandler.pcm)
    assert len(pcm) % 2 == 0
    assert ttfa is not None
    assert n_chunks > 1
    assert ttfa <= elapsed * 1000
    # First PCM should arrive after initial server delay, before full download finishes.
    assert ttfa >= _ChunkedFishHandler.delay_s * 1000 * 0.85
    assert elapsed > ttfa / 1000 + _ChunkedFishHandler.inter_chunk_delay_s * 0.5


class _VoiceHandler(BaseHTTPRequestHandler):
    bodies: list[dict[str, Any]] = []
    models: list[str] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _VoiceHandler.bodies.append(json.loads(self.rfile.read(length)))
        _VoiceHandler.models.append(self.headers.get("model", ""))
        pcm = b"\x01\x00" * 8
        self.send_response(200)
        self.send_header("Content-Type", "audio/pcm")
        self.send_header("Content-Length", str(len(pcm)))
        self.end_headers()
        self.wfile.write(pcm)


def test_rest_body_keeps_reference_id_when_the_argument_is_empty(tmp_path: Path) -> None:
    _VoiceHandler.bodies = []
    _VoiceHandler.models = []
    server = HTTPServer(("127.0.0.1", 0), _VoiceHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    configured = Settings(
        _env_file=None,
        fish_voice_id="voice-configured",
        fish_latency="balanced",
        fish_chunk_length=100,
        fish_audio_model="drama-3",
        call_settings_db=str(tmp_path / "calls.sqlite"),
    )
    bare = Settings(
        _env_file=None,
        fish_latency="balanced",
        fish_chunk_length=100,
        call_settings_db=str(tmp_path / "empty.sqlite"),
    )

    async def _speak(settings: Settings, text: str) -> None:
        async for _pcm, _ttfa in astream_fish_tts_pcm_rest(
            text,
            settings=settings,
            api_key="test-key",
            model=settings.effective_fish_model(),
            voice_id=None,
            client=client,
            tts_url="/v1/tts",
        ):
            pass

    try:
        asyncio.run(_speak(configured, "Vielen Dank, dann meldet sich Erika Muster."))
        asyncio.run(_speak(bare, "Das Fenster ist offen."))
    finally:
        asyncio.run(client.aclose())
        server.shutdown()

    assert _VoiceHandler.bodies[0]["reference_id"] == "voice-configured"
    assert _VoiceHandler.models[0] == "drama-3"
    assert "reference_id" not in _VoiceHandler.bodies[1]


def test_fish_chunk_length_validation() -> None:
    with pytest.raises(ValueError, match="FERNRUF_FISH_CHUNK_LENGTH"):
        Settings(_env_file=None, fish_chunk_length=50)
