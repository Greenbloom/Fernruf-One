from __future__ import annotations

import logging
import math
import struct
import uuid
from pathlib import Path

from fernruf.settings import Settings
from fernruf.tts.fish import FishTtsError, fish_tts_pcm_rest, fish_tts_pcm_ws
from fernruf.tts.pcm_codec import pcm16_mono_to_alaw, resample_pcm16_mono

LOG = logging.getLogger("fernruf")

M1_PROMPT_GLOB = "m1-prompt*"
M1_TEST_SENTENCE = "Dies ist ein Testanruf des Fernruf-Systems."


def _pcm_to_alaw(pcm16: bytes, sample_rate: int, target_rate: int = 8000) -> bytes:
    if sample_rate != target_rate:
        pcm16 = resample_pcm16_mono(pcm16, sample_rate, target_rate)
    return pcm16_mono_to_alaw(pcm16)


def _fixture_pcm(text_hint: str, sample_rate: int = 8000, duration_s: float = 6.0) -> bytes:
    n = int(sample_rate * duration_s)
    seed = sum(ord(c) for c in text_hint) % 997
    out: list[int] = []
    for i in range(n):
        t = i / sample_rate
        freq = 440 + (seed % 200)
        if int(t * 4) % 2 == 0:
            sample = int(8000 * math.sin(2 * math.pi * freq * t))
        else:
            sample = int(4000 * math.sin(2 * math.pi * (freq + 110) * t))
        out.append(max(-32768, min(32767, sample)))
    return struct.pack(f"<{n}h", *out)


def purge_m1_prompt_artifacts(sounds_dir: Path) -> None:
    """Remove prior m1-prompt files so Asterisk cannot pick a stale sibling format."""
    if not sounds_dir.is_dir():
        return
    for path in sounds_dir.glob(M1_PROMPT_GLOB):
        with_path = path
        try:
            with_path.unlink(missing_ok=True)
        except OSError:
            LOG.warning("could not remove stale prompt file %s", path)


def prepare_m1_prompt(settings: Settings) -> tuple[str, str]:
    """Synthesize prompt audio; return (source label, ARI media URI)."""
    sounds = Path(settings.sounds_dir)
    sounds.mkdir(parents=True, exist_ok=True)
    purge_m1_prompt_artifacts(sounds)

    stem = f"m1-prompt-{uuid.uuid4().hex}"
    dest_alaw = sounds / f"{stem}.alaw"
    source = _write_m1_prompt(settings, dest_alaw)
    return source, f"sound:custom/{stem}"


def _m1_spoken_text(settings: Settings) -> str:
    from fernruf.disclosure import spoken_ai_disclosure

    disclosure = spoken_ai_disclosure(settings)
    return f"{disclosure} {M1_TEST_SENTENCE}".strip()


def _write_m1_prompt(settings: Settings, dest_alaw: Path) -> str:
    """Return source label: fish-rest|fish-ws|fixture."""
    text = _m1_spoken_text(settings)

    if settings.fish_audio_api_key.strip():
        api_key = settings.fish_audio_api_key
        model = settings.fish_audio_model
        voice = settings.fish_voice_id or None
        fish_errors: list[str] = []
        for label, fn in (
            ("fish-rest", lambda: fish_tts_pcm_rest(text, api_key=api_key, model=model, voice_id=voice)),
            ("fish-ws", lambda: fish_tts_pcm_ws(text, api_key=api_key, model=model, voice_id=voice)),
        ):
            try:
                pcm, rate = fn()
                alaw = _pcm_to_alaw(pcm, rate)
                dest_alaw.write_bytes(alaw)
                return label
            except FishTtsError as exc:
                fish_errors.append(exc.reason)
                if exc.reason != "insufficient_balance":
                    LOG.warning(
                        "Fish TTS unavailable (%s): %s, trying next path",
                        exc.reason,
                        exc.detail or exc,
                    )
            except Exception as exc:
                fish_errors.append(label)
                LOG.warning("Fish TTS failed (%s): %s", label, exc)

        if fish_errors and all(e == "insufficient_balance" for e in fish_errors):
            LOG.warning("Fish TTS unavailable: insufficient_balance, using fixture prompt")
        else:
            LOG.warning("Fish TTS unavailable: all paths failed, using fixture prompt")

    pcm = _fixture_pcm(text)
    alaw = _pcm_to_alaw(pcm, 8000)
    dest_alaw.write_bytes(alaw)
    return "fixture"


def synthesize_m1_prompt(settings: Settings, dest_alaw: Path) -> str:
    """Legacy helper: write to dest_alaw after purging siblings in that directory."""
    purge_m1_prompt_artifacts(dest_alaw.parent)
    return _write_m1_prompt(settings, dest_alaw)
