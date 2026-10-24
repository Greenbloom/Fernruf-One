from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from fernruf.settings import Settings
from fernruf.tts.rest_stream import pcm_to_wav
from fernruf.tts.audio import _fixture_pcm
from pipecat.frames.frames import TTSAudioRawFrame

from fernruf.voice.services.fish_rest_tts import FishRestTTSService, _FISH_STOP_FRAME_TIMEOUT_S


def test_fish_run_tts_survives_slow_http(tmp_path: Path) -> None:
    pcm = _fixture_pcm("slow", sample_rate=16000, duration_s=0.4)
    wav = tmp_path / "slow.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))
    settings = Settings(_env_file=None, fish_tts_rest_fixture=str(wav))

    async def slow_stream(text: str, *, settings: Settings, **kwargs):  # noqa: ANN003
        await asyncio.sleep(3.5)
        yield pcm, 16000, None

    tts = FishRestTTSService(call_settings=settings)
    assert tts._stop_frame_timeout_s >= 60.0

    async def collect() -> list:
        frames = []
        async for frame in tts.run_tts("Hallo.", context_id="ctx-slow"):
            if frame is not None:
                frames.append(frame)
        return frames

    with patch("fernruf.voice.services.fish_rest_tts.stream_tts_sentences", slow_stream):
        frames = asyncio.run(collect())
    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    audio = b"".join(f.audio for f in audio_frames)
    assert len(audio) > 1000


def test_fish_stop_timeout_constant() -> None:
    assert _FISH_STOP_FRAME_TIMEOUT_S >= 90.0
