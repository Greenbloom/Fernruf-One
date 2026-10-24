from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from fernruf.settings import Settings
from fernruf.tts.fish import FishTtsError, TTS_FORMAT, TTS_SAMPLE_RATE, _decode_fish_audio_body

LOG = logging.getLogger("fernruf.tts.fish")

_DEFAULT_FISH_TTS_URL = "https://api.fish.audio/v1/tts"

_shared_async_client: httpx.AsyncClient | None = None


def get_shared_fish_rest_client(*, base_url: str | None = None) -> httpx.AsyncClient:
    """Reuse one async HTTP client across Fish REST TTS requests in a process."""
    global _shared_async_client
    if _shared_async_client is None or _shared_async_client.is_closed:
        _shared_async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(90.0, connect=20.0),
            base_url=base_url or "https://api.fish.audio",
        )
    return _shared_async_client


def _align_pcm16_chunks(buffer: bytes, chunk: bytes) -> tuple[bytes, list[bytes]]:
    """Append ``chunk`` to ``buffer`` and emit whole 16-bit samples only."""
    buffer += chunk
    out: list[bytes] = []
    emit_len = len(buffer) - (len(buffer) % 2)
    if emit_len:
        out.append(buffer[:emit_len])
        buffer = buffer[emit_len:]
    return buffer, out


async def astream_fish_tts_pcm_rest(
    text: str,
    *,
    settings: Settings,
    api_key: str,
    model: str,
    voice_id: str | None,
    rest_fixture_path: str | None = None,
    sample_rate: int = TTS_SAMPLE_RATE,
    audio_format: str = TTS_FORMAT,
    client: httpx.AsyncClient | None = None,
    tts_url: str | None = None,
) -> AsyncIterator[tuple[bytes, float | None]]:
    """Stream PCM chunks from Fish ``POST /v1/tts``; third tuple item is TTFA ms on first chunk only."""
    if rest_fixture_path:
        from pathlib import Path

        path = Path(rest_fixture_path)
        raw = path.read_bytes()
        pcm, _rate = _decode_fish_audio_body(
            raw,
            content_type="audio/wav" if raw.startswith(b"RIFF") else "audio/pcm",
            requested_format=audio_format,
            sample_rate=sample_rate,
        )
        step = max(2, sample_rate // 25 * 2)
        for i in range(0, len(pcm), step):
            yield pcm[i : i + step], (0.0 if i == 0 else None)
        return

    body: dict[str, Any] = {
        "text": text,
        "format": audio_format,
        "sample_rate": sample_rate,
        "latency": settings.fish_latency,
        "chunk_length": settings.fish_chunk_length,
    }
    if voice_id:
        body["reference_id"] = voice_id
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": model,
    }
    http = client or get_shared_fish_rest_client()
    path = tts_url or "/v1/tts"
    backoff_s = (0.15, 0.4)
    last_err: FishTtsError | None = None
    for attempt, delay_s in enumerate((0.0, *backoff_s)):
        if delay_s:
            await asyncio.sleep(delay_s)
        t0 = time.monotonic()
        first_audio = True
        pending = b""
        try:
            async with http.stream("POST", path, headers=headers, json=body) as response:
                content_type = response.headers.get("content-type", "")
                if response.status_code == 402:
                    body_text = (await response.aread())[:200].decode("utf-8", errors="replace")
                    raise FishTtsError("insufficient_balance", body_text)
                if response.status_code >= 500 or response.status_code == 429:
                    body_text = (await response.aread())[:200].decode("utf-8", errors="replace")
                    last_err = FishTtsError(f"http_{response.status_code}", body_text)
                    LOG.warning(
                        "Fish REST retryable %s (attempt %s/%s) text=%r",
                        response.status_code,
                        attempt + 1,
                        1 + len(backoff_s),
                        text[:60],
                    )
                    continue
                if response.status_code >= 400:
                    body_text = (await response.aread())[:200].decode("utf-8", errors="replace")
                    raise FishTtsError(f"http_{response.status_code}", body_text)

                if "wav" in content_type.lower():
                    raw = await response.aread()
                    pcm, _rate = _decode_fish_audio_body(
                        raw,
                        content_type=content_type,
                        requested_format=audio_format,
                        sample_rate=sample_rate,
                    )
                    ttfa_ms = (time.monotonic() - t0) * 1000
                    LOG.info("Fish REST TTFA %.0fms (wav) chars=%s", ttfa_ms, len(text))
                    step = max(2, sample_rate // 25 * 2)
                    for i in range(0, len(pcm), step):
                        yield pcm[i : i + step], (ttfa_ms if i == 0 else None)
                    return

                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    if first_audio:
                        ttfa_ms = (time.monotonic() - t0) * 1000
                        LOG.info(
                            "Fish REST TTFA %.0fms latency=%s chunk_length=%s chars=%s",
                            ttfa_ms,
                            settings.fish_latency,
                            settings.fish_chunk_length,
                            len(text),
                        )
                        first_audio = False
                    else:
                        ttfa_ms = None
                    pending, emits = _align_pcm16_chunks(pending, chunk)
                    for emit in emits:
                        yield emit, ttfa_ms
                        ttfa_ms = None

                if len(pending) >= 2:
                    emit_len = len(pending) - (len(pending) % 2)
                    yield pending[:emit_len], None
                return
        except (httpx.HTTPError, OSError) as exc:
            last_err = FishTtsError("connection_error", str(exc))
            LOG.warning(
                "Fish REST connection error (attempt %s/%s): %s",
                attempt + 1,
                1 + len(backoff_s),
                exc,
            )
            continue
    if last_err:
        raise last_err
    raise FishTtsError("http_unknown", "Fish REST TTS failed after retries")


async def warm_fish_rest_connection(
    *,
    settings: Settings,
    api_key: str,
    model: str,
    voice_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Cheap POST to keep HTTP/2 warm at call start (discarded audio)."""
    if not api_key.strip() or settings.fish_tts_rest_fixture:
        return
    http = client or get_shared_fish_rest_client()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": model,
    }
    body: dict[str, Any] = {
        "text": ".",
        "format": TTS_FORMAT,
        "sample_rate": TTS_SAMPLE_RATE,
        "latency": settings.fish_latency,
        "chunk_length": settings.fish_chunk_length,
    }
    if voice_id:
        body["reference_id"] = voice_id
    try:
        async with http.stream("POST", "/v1/tts", headers=headers, json=body) as response:
            if response.status_code >= 400:
                return
            async for _ in response.aiter_bytes():
                break
        LOG.info("Fish REST connection warm complete model=%s", model)
    except Exception as exc:
        LOG.debug("Fish REST warm skipped: %s", exc)
