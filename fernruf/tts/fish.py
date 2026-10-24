from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

LOG = logging.getLogger("fernruf.tts.fish")

TTS_SAMPLE_RATE = 16_000
TTS_FORMAT = "pcm"


@dataclass
class FishTtsError(Exception):
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return self.reason if not self.detail else f"{self.reason}: {self.detail}"


def _decode_fish_audio_body(
    content: bytes,
    *,
    content_type: str,
    requested_format: str,
    sample_rate: int,
) -> tuple[bytes, int]:
    ct = (content_type or "").lower()
    if not content:
        raise FishTtsError("empty_body", "Fish TTS returned no audio bytes")

    if content.startswith(b"RIFF") or "wav" in ct:
        with wave.open(io.BytesIO(content), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise FishTtsError(
                    "unsupported_wav",
                    f"channels={wf.getnchannels()} width={wf.getsampwidth()}",
                )
            rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        return pcm, rate

    if requested_format == "pcm" or "audio/pcm" in ct or "audio/L16" in ct:
        return content, sample_rate

    raise FishTtsError(
        "unknown_format",
        f"content-type={content_type!r} first_bytes={content[:16]!r}",
    )


def fish_tts_pcm_rest(
    text: str,
    *,
    api_key: str,
    model: str,
    voice_id: str | None,
    rest_fixture_path: str | None = None,
    sample_rate: int = TTS_SAMPLE_RATE,
    audio_format: str = TTS_FORMAT,
) -> tuple[bytes, int]:
    """POST /v1/tts with model header (s2.1-pro-free on the host)."""
    if rest_fixture_path:
        path = Path(rest_fixture_path)
        raw = path.read_bytes()
        pcm, rate = _decode_fish_audio_body(
            raw,
            content_type="audio/wav" if raw.startswith(b"RIFF") else "audio/pcm",
            requested_format=audio_format,
            sample_rate=sample_rate,
        )
        LOG.info(
            "Fish REST fixture %s bytes=%s -> pcm=%s rate=%s",
            path.name,
            len(raw),
            len(pcm),
            rate,
        )
        return pcm, rate

    body: dict[str, Any] = {
        "text": text,
        "format": audio_format,
        "sample_rate": sample_rate,
        "latency": "balanced",
        "chunk_length": 100,
    }
    if voice_id:
        body["reference_id"] = voice_id
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": model,
    }
    with httpx.Client(timeout=90.0) as client:
        r = client.post("https://api.fish.audio/v1/tts", headers=headers, json=body)
    content_type = r.headers.get("content-type", "")
    LOG.info(
        "Fish REST TTS status=%s content-type=%s bytes=%s",
        r.status_code,
        content_type,
        len(r.content),
    )
    if r.status_code == 402:
        raise FishTtsError("insufficient_balance", r.text[:200])
    if r.status_code >= 400:
        raise FishTtsError(f"http_{r.status_code}", r.text[:200])
    return _decode_fish_audio_body(
        r.content,
        content_type=content_type,
        requested_format=audio_format,
        sample_rate=sample_rate,
    )


async def _fish_ws(text: str, *, api_key: str, model: str, voice_id: str | None) -> tuple[bytes, int]:
    import websockets

    url = "wss://api.fish.audio/v1/tts/live"
    headers = {"Authorization": f"Bearer {api_key}", "model": model}
    pcm_chunks: list[bytes] = []
    sample_rate = 44100

    try:
        async with websockets.connect(url, additional_headers=headers) as ws:
            start: dict[str, Any] = {
                "event": "start",
                "request": {
                    "text": "",
                    "latency": "balanced",
                    "format": "pcm",
                    "model": model,
                },
            }
            if voice_id:
                start["request"]["reference_id"] = voice_id
            await ws.send(json.dumps(start))
            await ws.send(json.dumps({"event": "text", "text": text}))
            await ws.send(json.dumps({"event": "flush"}))
            await ws.send(json.dumps({"event": "stop"}))

            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    pcm_chunks.append(msg)
                    continue
                data = json.loads(msg)
                if data.get("event") == "error":
                    raise FishTtsError("websocket_error", json.dumps(data)[:200])
                if data.get("event") in ("finish", "stop"):
                    sample_rate = int(data.get("sample_rate", sample_rate))
                    break
    except websockets.exceptions.InvalidStatusCode as exc:
        if exc.status_code == 402:
            raise FishTtsError("insufficient_balance", str(exc)) from exc
        raise FishTtsError(f"websocket_http_{exc.status_code}", str(exc)) from exc

    return b"".join(pcm_chunks), sample_rate


def fish_tts_pcm_ws(
    text: str,
    *,
    api_key: str,
    model: str,
    voice_id: str | None,
) -> tuple[bytes, int]:
    return asyncio.run(_fish_ws(text, api_key=api_key, model=model, voice_id=voice_id))
