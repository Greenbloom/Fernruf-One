from __future__ import annotations

import warnings

import numpy as np

_AUDIOOP: object | None = None


def _audioop_module():
    global _AUDIOOP
    if _AUDIOOP is not None:
        return _AUDIOOP
    try:
        import audioop_lts as mod
    except ImportError:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            import audioop as mod  # type: ignore[no-redef]
    _AUDIOOP = mod
    return mod


def pcm16_mono_to_alaw(pcm16: bytes) -> bytes:
    if not pcm16:
        return b""
    return _audioop_module().lin2alaw(pcm16, 2)


def resample_pcm16_mono(pcm16: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm16:
        return pcm16
    samples = np.frombuffer(pcm16, dtype=np.int16)
    if samples.size == 0:
        return pcm16
    dst_count = max(1, int(round(samples.size * dst_rate / src_rate)))
    x_old = np.arange(samples.size, dtype=np.float64)
    x_new = np.linspace(0, samples.size - 1, dst_count, dtype=np.float64)
    resampled = np.interp(x_new, x_old, samples.astype(np.float64))
    return np.clip(np.round(resampled), -32768, 32767).astype(np.int16).tobytes()
