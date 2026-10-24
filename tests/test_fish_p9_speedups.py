from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fernruf.settings import Settings
from fernruf.tts.fish_audio_cache import read_fish_cache, write_fish_cache
from fernruf.tts.fish_rest_pipeline import FishRestPipeline


async def _fake_astream(text: str, **kwargs) -> AsyncIterator[tuple[bytes, float | None]]:  # noqa: ANN003
    calls: list[str] = kwargs.get("_calls") or []
    calls.append(text)
    yield (text.encode()[:4] or b"x") * 50, 1.0


def test_fish_cache_hit(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        call_settings_db=str(tmp_path / "db.sqlite"),
        fish_latency="balanced",
    )
    write_fish_cache(
        settings,
        text="Einen Moment…",
        voice_id="v1",
        model="s2.1-pro-free",
        latency="balanced",
        pcm=b"\x01\x02" * 100,
    )
    assert read_fish_cache(
        settings,
        text="Einen Moment…",
        voice_id="v1",
        model="s2.1-pro-free",
        latency="balanced",
    )


def test_pipeline_prefetch_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        calls.append(text)
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr(
        "fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest",
        _stream,
    )
    settings = Settings(_env_file=None, fish_first_clause=True, fish_latency="balanced")
    pipeline = FishRestPipeline(
        settings=settings,
        api_key="k",
        model="m",
        voice_id=None,
    )

    async def run() -> None:
        async for _pcm, _ in pipeline.stream_text(
            "Eins zwei drei vier, die Verbindung steht und klingt gut."
        ):
            pass

    asyncio.run(run())
    assert len(calls) == 2
    assert calls[0].startswith("Eins zwei drei vier,")


def test_pipeline_cancel_stops_early(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _slow(text: str, **kwargs):  # noqa: ANN003
        calls.append(text)
        yield b"\x01\x02" * 20, 1.0
        await asyncio.sleep(0.05)
        yield b"\x03\x04" * 20, None

    monkeypatch.setattr(
        "fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest",
        _slow,
    )
    settings = Settings(_env_file=None, fish_first_clause=True, fish_latency="balanced")
    pipeline = FishRestPipeline(
        settings=settings,
        api_key="k",
        model="m",
        voice_id=None,
    )

    async def run() -> int:
        n = 0
        async for _pcm, _ in pipeline.stream_text(
            "Eins zwei drei vier, die Verbindung steht und klingt gut."
        ):
            n += 1
            if n == 1:
                pipeline.cancel()
        return n

    n = asyncio.run(run())
    assert n >= 1
    assert len(calls) <= 2
