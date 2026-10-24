from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fernruf.settings import Settings
from fernruf.tts.rest_stream import pcm_to_wav
from fernruf.tts.audio import _fixture_pcm
from pipecat.frames.frames import TTSAudioRawFrame

from fernruf.voice.services.fish_rest_tts import FishRestTTSService


@pytest.fixture
def fish_wav_fixture(tmp_path: Path) -> Path:
    pcm = _fixture_pcm("verify", sample_rate=16000, duration_s=0.8)
    path = tmp_path / "fish.wav"
    path.write_bytes(pcm_to_wav(pcm, 16000))
    return path


def test_fish_rest_tts_run_tts_from_recorded_wav(fish_wav_fixture: Path) -> None:
    settings = Settings(
        _env_file=None,
        fish_tts_rest_fixture=str(fish_wav_fixture),
        fish_audio_api_key="",
    )
    tts = FishRestTTSService(call_settings=settings)
    async def collect() -> list:
        frames = []
        async for frame in tts.run_tts("Hallo.", context_id="ctx-test"):
            if frame is not None:
                frames.append(frame)
        return frames

    frames = asyncio.run(collect())
    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    audio = b"".join(f.audio for f in audio_frames)
    assert len(audio) > 1600
    assert all(f.sample_rate == 16000 for f in audio_frames)
    assert all(f.context_id == "ctx-test" for f in audio_frames)


def test_fish_rest_fixture_decode(fish_wav_fixture: Path) -> None:
    from fernruf.tts.fish import fish_tts_pcm_rest

    pcm, rate = fish_tts_pcm_rest(
        "x",
        api_key="",
        model="s2.1-pro-free",
        voice_id=None,
        rest_fixture_path=str(fish_wav_fixture),
    )
    assert rate == 16000
    assert len(pcm) > 1000
