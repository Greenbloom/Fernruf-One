from __future__ import annotations

from dataclasses import dataclass

from pipecat.frames.frames import Frame


@dataclass
class FishTtsFirstAudioFrame(Frame):
    """Emitted when Fish returns the first PCM chunk for an utterance."""

    ttfa_ms: float


@dataclass
class LlmInferenceStartedFrame(Frame):
    """Marks the start of an outbound LLM HTTP streaming request for this turn."""


@dataclass
class LlmFirstTokenFrame(Frame):
    """First streamed LLM delta (text or tool call) for the active turn."""
