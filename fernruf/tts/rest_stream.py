from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from collections.abc import AsyncIterator

from fernruf.settings import Settings
from fernruf.tts.fish import FishTtsError, TTS_SAMPLE_RATE
from fernruf.tts.fish_audio_cache import read_fish_cache, write_fish_cache
from fernruf.tts.fish_rest_client import astream_fish_tts_pcm_rest
from fernruf.tts.fish_rest_pipeline import FishRestPipeline
from fernruf.tts.fish_ws_session import FishLiveWsSession

LOG = logging.getLogger("fernruf.tts.rest")

SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")

_STANDARD_GOODBYES = frozenset(
    {
        "Auf Wiederhören!",
        "Auf Wiederhören.",
        "Vielen Dank für den Testanruf. Auf Wiederhören!",
        "Vielen Dank und auf Wiederhören!",
        "Tschüss!",
        "Schönen Tag noch!",
    }
)


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _should_cache_phrase(settings: Settings, text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if t in _STANDARD_GOODBYES:
        return True
    if t == settings.disclosure_prefix.strip():
        return True
    if t == settings.filler_phrase.strip():
        return True
    phrases = getattr(settings, "call_phrases", None) or {}
    goodbye = ""
    if isinstance(phrases, dict):
        goodbye = str(phrases.get("goodbye") or "").strip()
    if goodbye and t == goodbye:
        return True
    return False


def _cache_language(settings: Settings) -> str:
    return (getattr(settings, "call_language", "") or "").strip().lower()


async def stream_tts_sentences(
    text: str,
    *,
    settings: Settings,
    tts_url: str | None = None,
    http_client: object | None = None,
    pipeline: FishRestPipeline | None = None,
    ws_session: FishLiveWsSession | None = None,
) -> AsyncIterator[tuple[bytes, int, float | None]]:
    """Fish TTS per sentence; yields (pcm_chunk, sample_rate, ttfa_ms)."""
    if settings.voice_mock and not settings.fish_tts_rest_fixture:
        yield b"\x00\x00" * 8000, TTS_SAMPLE_RATE, 0.0
        return
    if not settings.fish_audio_api_key and not settings.fish_tts_rest_fixture:
        yield b"\x00\x00" * 8000, TTS_SAMPLE_RATE, 0.0
        return

    model = settings.effective_fish_model()
    voice = settings.fish_voice_id or ""

    for sentence in split_sentences(text):
        if _should_cache_phrase(settings, sentence):
            cached = read_fish_cache(
                settings,
                text=sentence,
                voice_id=voice,
                model=model,
                latency=settings.fish_latency,
                language=_cache_language(settings),
            )
            if cached:
                step = max(2, TTS_SAMPLE_RATE // 25 * 2)
                for i in range(0, len(cached), step):
                    yield cached[i : i + step], TTS_SAMPLE_RATE, (0.0 if i == 0 else None)
                continue

        use_ws = (
            settings.fish_prefers_websocket()
            and ws_session is not None
            and not ws_session.fallback_rest
        )
        if use_ws:
            try:
                async for pcm_chunk, ttfa_ms in ws_session.stream_utterance(sentence):
                    yield pcm_chunk, TTS_SAMPLE_RATE, ttfa_ms
                if _should_cache_phrase(settings, sentence):
                    pass
                continue
            except FishTtsError:
                ws_session.fallback_rest = True

        try:
            stream = (
                pipeline.stream_text(sentence)
                if pipeline is not None
                else astream_fish_tts_pcm_rest(
                    sentence,
                    settings=settings,
                    api_key=settings.fish_audio_api_key,
                    model=model,
                    voice_id=settings.fish_voice_id or None,
                    rest_fixture_path=settings.fish_tts_rest_fixture or None,
                    sample_rate=TTS_SAMPLE_RATE,
                    client=http_client,  # type: ignore[arg-type]
                    tts_url=tts_url,
                )
            )
            collected: list[bytes] = []
            async for pcm_chunk, ttfa_ms in stream:
                if _should_cache_phrase(settings, sentence):
                    collected.append(pcm_chunk)
                yield pcm_chunk, TTS_SAMPLE_RATE, ttfa_ms
            if collected and _should_cache_phrase(settings, sentence):
                write_fish_cache(
                    settings,
                    text=sentence,
                    voice_id=voice,
                    model=model,
                    latency=settings.fish_latency,
                    pcm=b"".join(collected),
                    language=_cache_language(settings),
                )
        except FishTtsError as exc:
            LOG.warning("Fish TTS failed for sentence %r: %s", sentence[:80], exc)
            fallback = settings.filler_phrase.strip() or "Einen Moment, die Verbindung stockt kurz."
            cached = read_fish_cache(
                settings,
                text=fallback,
                voice_id=voice,
                model=model,
                latency=settings.fish_latency,
                language=_cache_language(settings),
            )
            if cached:
                step = max(2, TTS_SAMPLE_RATE // 25 * 2)
                for i in range(0, len(cached), step):
                    yield cached[i : i + step], TTS_SAMPLE_RATE, (0.0 if i == 0 else None)
                continue
            try:
                async for pcm_chunk, ttfa_ms in astream_fish_tts_pcm_rest(
                    fallback,
                    settings=settings,
                    api_key=settings.fish_audio_api_key,
                    model=model,
                    voice_id=settings.fish_voice_id or None,
                    rest_fixture_path=settings.fish_tts_rest_fixture or None,
                    sample_rate=TTS_SAMPLE_RATE,
                    client=http_client,  # type: ignore[arg-type]
                    tts_url=tts_url,
                ):
                    yield pcm_chunk, TTS_SAMPLE_RATE, ttfa_ms
            except FishTtsError:
                yield b"\x00\x00" * 4000, TTS_SAMPLE_RATE, 0.0


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
