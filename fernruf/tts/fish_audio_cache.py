from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fernruf.settings import Settings
from fernruf.tts.fish import TTS_SAMPLE_RATE, _decode_fish_audio_body

LOG = logging.getLogger("fernruf.tts.fish_cache")


def fish_cache_dir(settings: Settings) -> Path:
    base = Path(settings.call_settings_db).parent
    path = base / "fish-tts-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fish_cache_key(
    text: str,
    *,
    voice_id: str,
    model: str,
    latency: str,
    language: str = "",
) -> str:
    lang = (language or "").strip().lower()
    payload = f"{lang}|{text.strip()}|{voice_id}|{model}|{latency}".encode()
    return hashlib.sha256(payload).hexdigest()


def read_fish_cache(
    settings: Settings,
    *,
    text: str,
    voice_id: str,
    model: str,
    latency: str,
    language: str = "",
) -> bytes | None:
    key = fish_cache_key(
        text, voice_id=voice_id, model=model, latency=latency, language=language
    )
    path = fish_cache_dir(settings) / f"{key}.pcm"
    if not path.is_file():
        return None
    LOG.info("Fish TTS cache hit (%s bytes) text=%s…", path.stat().st_size, text[:40])
    return path.read_bytes()


def write_fish_cache(
    settings: Settings,
    *,
    text: str,
    voice_id: str,
    model: str,
    latency: str,
    pcm: bytes,
    language: str = "",
) -> None:
    if not pcm:
        return
    key = fish_cache_key(
        text, voice_id=voice_id, model=model, latency=latency, language=language
    )
    path = fish_cache_dir(settings) / f"{key}.pcm"
    path.write_bytes(pcm)
    LOG.debug("Fish TTS cache store %s bytes -> %s", len(pcm), path.name)


def fixture_pcm_from_cache(raw: bytes, sample_rate: int = TTS_SAMPLE_RATE) -> bytes:
    if raw.startswith(b"RIFF"):
        pcm, _ = _decode_fish_audio_body(
            raw, content_type="audio/wav", requested_format="pcm", sample_rate=sample_rate
        )
        return pcm
    return raw
