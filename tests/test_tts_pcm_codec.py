from __future__ import annotations

import warnings

import numpy as np

from fernruf.tts.audio import _fixture_pcm, _pcm_to_alaw
from fernruf.tts.pcm_codec import _audioop_module, pcm16_mono_to_alaw, resample_pcm16_mono


def test_resample_pcm16_mono_halves_length_at_half_rate() -> None:
    pcm = _fixture_pcm("codec-test", sample_rate=16000, duration_s=0.05)
    down = resample_pcm16_mono(pcm, 16000, 8000)
    assert len(down) == len(np.frombuffer(pcm, dtype=np.int16)) // 2 * 2


def test_pcm16_mono_to_alaw_matches_audioop() -> None:
    audioop = _audioop_module()
    pcm8 = _fixture_pcm("alaw", sample_rate=8000, duration_s=0.05)
    ours = pcm16_mono_to_alaw(pcm8)
    ref = audioop.lin2alaw(pcm8, 2)
    assert ours == ref


def test_pcm_to_alaw_pipeline_produces_valid_alaw() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        audioop = _audioop_module()
    pcm = _fixture_pcm("compare", sample_rate=16000, duration_s=0.08)
    ours = _pcm_to_alaw(pcm, 16000, 8000)
    ref_pcm, _ = audioop.ratecv(pcm, 2, 1, 16000, 8000, None)
    ref = audioop.lin2alaw(ref_pcm, 2)
    assert len(ours) == len(ref)
    assert len(ours) > 0
