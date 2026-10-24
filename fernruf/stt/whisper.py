from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import numpy as np

from fernruf.settings import Settings
from fernruf.stt.cuda_libs import preload_nvidia_cuda_libs

LOG = logging.getLogger("fernruf.stt")

_model = None
_active_device: str | None = None
_active_compute: str | None = None


class SttPreloadError(RuntimeError):
    """STT could not be initialized (CUDA libs, model load, or warm-up transcribe)."""


@dataclass
class TranscriptSegment:
    text: str
    language: str


def active_stt_device() -> str | None:
    return _active_device


def _normalize_device_choice(settings: Settings) -> str:
    dev = (settings.whisper_device or "auto").strip().lower()
    if dev in ("auto", "cuda", "cpu"):
        return dev
    LOG.warning("unknown FERNRUF_STT_DEVICE=%r; using auto", settings.whisper_device)
    return "auto"


def _compute_for_device(device: str, settings: Settings) -> str:
    compute = settings.whisper_compute_type
    if device == "cpu" and compute.startswith("float16"):
        return "int8"
    return compute


def _load_model(settings: Settings, *, device: str) -> object:
    preload_nvidia_cuda_libs()
    from faster_whisper import WhisperModel

    compute = _compute_for_device(device, settings)
    LOG.info(
        "loading faster-whisper model=%s device=%s compute=%s",
        settings.whisper_model,
        device,
        compute,
    )
    return WhisperModel(settings.whisper_model, device=device, compute_type=compute)


def whisper_language_code(settings: Settings) -> str:
    """Language Whisper must use for this call.

    After ``prepare_call_settings``, that is the call language: the detected
    code, or ``FERNRUF_WHISPER_LANGUAGE`` when detection failed. The env value
    does not override a successful detection. Before prepare, the env value
    is the fallback and an empty one means German.
    """
    if getattr(settings, "_language_ready", False):
        ready = (getattr(settings, "call_language", "") or "").strip()
        if ready:
            return ready
    explicit = (settings.whisper_language or "").strip()
    if explicit:
        return explicit
    return (getattr(settings, "call_language", "") or "").strip() or "de"


def whisper_initial_prompt(settings: Settings) -> str:
    """Prompt for the call language, from the prepared table or the static one."""
    from fernruf.language import phrases_for_settings
    from fernruf.principal import PHRASE_TEMPLATES

    if not (settings.principal_name or "").strip():
        return PHRASE_TEMPLATES["de"]["whisper_initial_prompt"]
    prompt = str(phrases_for_settings(settings).get("whisper_initial_prompt") or "").strip()
    return prompt or "Telefonat auf Deutsch."


def _warmup_transcribe(model: object, settings: Settings) -> None:
    """Run one short transcribe so CUDA/cuBLAS errors surface before a call."""
    n = 8000  # 0.5 s silence at 16 kHz
    silence = np.zeros(n, dtype=np.float32)
    segments, _info = model.transcribe(  # type: ignore[union-attr]
        silence,
        language=whisper_language_code(settings),
        beam_size=1,
        vad_filter=False,
    )
    list(segments)


def _try_init_on_device(settings: Settings, device: str) -> object:
    model = _load_model(settings, device=device)
    _warmup_transcribe(model, settings)
    return model


def prepare_stt_for_call(settings: Settings) -> str:
    """Load faster-whisper and warm up; return device in use (cpu or cuda)."""
    if settings.voice_mock:
        return "mock"

    global _model, _active_device, _active_compute
    if _model is not None and _active_device:
        return _active_device

    if os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in ("1", "true", "yes"):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    choice = _normalize_device_choice(settings)
    errors: list[str] = []

    if choice in ("auto", "cuda"):
        try:
            _model = _try_init_on_device(settings, "cuda")
            _active_device = "cuda"
            _active_compute = _compute_for_device("cuda", settings)
            return _active_device
        except Exception as exc:
            msg = str(exc)
            errors.append(f"cuda: {msg}")
            _model = None
            if choice == "cuda":
                raise SttPreloadError(
                    "CUDA STT failed during preload. Install Linux wheels "
                    "(nvidia-cublas-cu12, nvidia-cudnn-cu12) via uv sync, or set "
                    "FERNRUF_STT_DEVICE=cpu. "
                    f"Detail: {msg}"
                ) from exc
            LOG.warning(
                "CUDA STT unavailable (%s); falling back to CPU int8. "
                "Set FERNRUF_STT_DEVICE=cuda to require GPU.",
                msg,
            )

    try:
        cpu_settings = settings
        _model = _try_init_on_device(cpu_settings, "cpu")
        _active_device = "cpu"
        _active_compute = _compute_for_device("cpu", settings)
        return _active_device
    except Exception as exc:
        errors.append(f"cpu: {exc}")
        _model = None
        raise SttPreloadError(
            "STT preload failed on all devices. "
            + "; ".join(errors)
        ) from exc


def preload_whisper_model(settings: Settings) -> None:
    prepare_stt_for_call(settings)


_logged_live_stt: set[int] = set()


def _transcribe_kwargs(settings: Settings) -> dict[str, object]:
    # vad_filter: faster-whisper's own VAD, on top of the pipeline VAD that
    # already limits run_stt to speech segments. condition_on_previous_text
    # stays off so silence does not repeat the previous phrase.
    return {
        "language": whisper_language_code(settings),
        "beam_size": 1,
        "vad_filter": True,
        "condition_on_previous_text": False,
        "initial_prompt": whisper_initial_prompt(settings),
    }


_HALLUCINATION_EXACT = frozenset(
    {
        "vielen dank",
        "danke",
        "danke schön",
        "danke schoen",
        "thanks",
        "thanks for watching",
        "thank you",
        "thank you for watching",
    }
)


def _phrase_key(text: str) -> str:
    folded = re.sub(r"[^\w\s]", " ", (text or "").casefold(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", folded).strip()


def is_lone_hallucination(text: str) -> bool:
    """True when every sentence is only a known silence hallucination."""
    parts = [part.strip() for part in re.split(r"[.!?…;]+", text or "") if part.strip()]
    if not parts:
        return False
    for part in parts:
        key = _phrase_key(part)
        if key in _HALLUCINATION_EXACT or key.startswith("untertitel"):
            continue
        return False
    return True


def stt_drop_reason(
    text: str,
    *,
    no_speech_prob: float,
    avg_logprob: float,
    no_speech_threshold: float = 0.6,
    min_avg_logprob: float = -1.0,
) -> str:
    """Why this segment should be discarded, or '' to keep it.

    Defaults match ``FERNRUF_WHISPER_NO_SPEECH_PROB`` (0.6) and
    ``FERNRUF_WHISPER_MIN_AVG_LOGPROB`` (-1.0). A known hallucination phrase
    is dropped only when it is the whole segment and that segment is already
    below those bars. A confident 'Vielen Dank' is kept.
    """
    low_speech = no_speech_prob >= no_speech_threshold
    low_log = avg_logprob < min_avg_logprob
    if (low_speech or low_log) and is_lone_hallucination(text):
        return "hallucination_phrase"
    if low_speech:
        return "no_speech_prob"
    if low_log:
        return "avg_logprob"
    return ""


def log_live_stt_kwargs(settings: Settings) -> dict[str, object]:
    """Log the kwargs the live transcribe path will pass to faster-whisper."""
    kw = _transcribe_kwargs(settings)
    marker = id(settings)
    if marker not in _logged_live_stt:
        if len(_logged_live_stt) > 32:
            _logged_live_stt.clear()
        _logged_live_stt.add(marker)
        LOG.info("STT transcribe kwargs (live path): %s", kw)
    return kw


def transcribe_pcm(
    pcm_s16le: bytes,
    *,
    sample_rate: int,
    settings: Settings,
    mock_text: str | None = None,
) -> TranscriptSegment:
    if settings.voice_mock or mock_text is not None:
        return TranscriptSegment(
            text=mock_text or "Ja, das passt.",
            language=whisper_language_code(settings),
        )

    global _model
    if _model is None:
        prepare_stt_for_call(settings)

    kw = log_live_stt_kwargs(settings)

    audio = np.frombuffer(pcm_s16le, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segments, _info = _model.transcribe(  # type: ignore[union-attr]
            audio,
            **kw,
        )
        kept: list[str] = []
        for segment in segments:
            text = (getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            reason = stt_drop_reason(
                text,
                no_speech_prob=float(getattr(segment, "no_speech_prob", 0.0) or 0.0),
                avg_logprob=float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                no_speech_threshold=float(settings.whisper_no_speech_prob),
                min_avg_logprob=float(settings.whisper_min_avg_logprob),
            )
            if reason:
                LOG.info(
                    "STT dropped segment reason=%s no_speech_prob=%.2f avg_logprob=%.2f text=%s",
                    reason,
                    float(getattr(segment, "no_speech_prob", 0.0) or 0.0),
                    float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                    text[:80],
                )
                continue
            kept.append(text)
        text = " ".join(kept)
    except Exception as exc:
        LOG.warning("STT transcribe failed on %s: %s", _active_device, exc)
        return TranscriptSegment(text="", language=whisper_language_code(settings))
    return TranscriptSegment(text=text or "", language=whisper_language_code(settings))


def transcribe_wav_file(path: str, *, settings: Settings) -> TranscriptSegment:
    import wave

    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise SttPreloadError(f"expected mono 16-bit PCM WAV, got {path}")
        rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    if rate != 16000:
        from scipy import signal

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        n_out = int(len(audio) * 16000 / rate)
        audio = signal.resample(audio, n_out)
        pcm = np.clip(audio, -32768, 32767).astype(np.int16).tobytes()
    return transcribe_pcm(pcm, sample_rate=16000, settings=settings)
