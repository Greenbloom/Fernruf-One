from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from fernruf.mood import MoodTaggedWs, MoodTurn
from fernruf.settings import Settings
from fernruf.voice.frames.metrics_frames import FishTtsFirstAudioFrame
from fernruf.tts.audio import _fixture_pcm
from fernruf.tts.fish_rest_client import warm_fish_rest_connection
from fernruf.tts.fish_rest_pipeline import FishRestPipeline
from fernruf.tts.fish_ws_session import FishLiveWsSession
from fernruf.tts.rest_stream import stream_tts_sentences

LOG = logging.getLogger("fernruf.voice.tts")

_OUT_RATE = 16000
_CHUNK_BYTES = int(_OUT_RATE * 0.02 * 2)
_FISH_STOP_FRAME_TIMEOUT_S = 120.0


class FishRestTTSService(TTSService):
    """Fish Audio REST/WebSocket TTS, resampled to slin16 for Asterisk WS."""

    def __init__(
        self,
        *,
        call_settings: Settings,
        use_fixture: bool = False,
        synth_log: list[str] | None = None,
        mood: MoodTurn | None = None,
        **kwargs
    ) -> None:
        voice = call_settings.fish_voice_id or None
        model = call_settings.effective_fish_model()
        super().__init__(
            sample_rate=_OUT_RATE,
            push_start_frame=True,
            push_stop_frames=True,
            stop_frame_timeout_s=_FISH_STOP_FRAME_TIMEOUT_S,
            max_consecutive_zero_audio_contexts=0,
            settings=TTSSettings(
                model=model,
                voice=voice,
                language="de",
            ),
            **kwargs,
        )
        self._call_settings = call_settings
        self._use_fixture = use_fixture
        self._synth_log = synth_log
        self._mood = mood
        self._resampler = create_stream_resampler()
        self._pipeline = FishRestPipeline(
            settings=call_settings,
            api_key=call_settings.fish_audio_api_key,
            model=model,
            voice_id=voice,
            rest_fixture_path=call_settings.fish_tts_rest_fixture or None,
            prepare_segment=mood.decorate_segment if mood is not None else None,
        )
        self._ws_session: FishLiveWsSession | None = None
        self._ws_for_stream: FishLiveWsSession | MoodTaggedWs | None = None
        if call_settings.fish_prefers_websocket():
            self._ws_session = FishLiveWsSession(
                settings=call_settings,
                api_key=call_settings.fish_audio_api_key,
                model=model,
                voice_id=voice,
            )
            self._ws_for_stream = (
                MoodTaggedWs(self._ws_session, mood.decorate_segment)
                if mood is not None
                else self._ws_session
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if self._mood is not None:
            if isinstance(frame, InterruptionFrame):
                self._mood.disarm_reply()
            elif isinstance(frame, LLMFullResponseStartFrame):
                self._mood.begin_reply()
            elif isinstance(frame, TTSSpeakFrame):
                self._mood.note_direct_speak()
        if isinstance(frame, StartFrame):
            asyncio.create_task(
                warm_fish_rest_connection(
                    settings=self._call_settings,
                    api_key=self._call_settings.fish_audio_api_key,
                    model=self._call_settings.effective_fish_model(),
                    voice_id=self._call_settings.fish_voice_id or None,
                )
            )
            if self._ws_session is not None:
                asyncio.create_task(self._ws_session.connect())
        if isinstance(frame, InterruptionFrame):
            self._pipeline.cancel()
            self._pipeline.reset_cancel()
            if self._ws_session is not None:
                asyncio.create_task(self._ws_session.cancel())
        await super().process_frame(frame, direction)
        if self._mood is not None and isinstance(frame, TTSSpeakFrame):
            self._mood.clear_direct_speak()
        # TTSService already forwarded this frame. Pushing an upstream
        # LLMMessagesAppendFrame again delivers the one consolidated spoken
        # utterance to the user aggregator twice, so every agent line is stored twice.
        if isinstance(frame, LLMMessagesAppendFrame) and direction == FrameDirection.UPSTREAM:
            return
        await self.push_frame(frame, direction)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        LOG.info("TTS start: %s", text[:120] + ("…" if len(text) > 120 else ""))
        if self._synth_log is not None:
            self._synth_log.append(text.strip())
        if self._use_fixture or (
            self._call_settings.voice_mock and not self._call_settings.fish_tts_rest_fixture
        ):
            pcm = _fixture_pcm(text, sample_rate=_OUT_RATE, duration_s=max(1.5, len(text) / 28))
            chunks = [pcm[i : i + _CHUNK_BYTES] for i in range(0, len(pcm), _CHUNK_BYTES)]
            for i, chunk in enumerate(chunks):
                if chunk:
                    if i == 0:
                        yield FishTtsFirstAudioFrame(ttfa_ms=0.0)
                    yield TTSAudioRawFrame(
                        audio=chunk,
                        sample_rate=_OUT_RATE,
                        num_channels=1,
                        context_id=context_id,
                    )
            return

        first_audio = True
        async for pcm, rate, ttfa_ms in stream_tts_sentences(
            text,
            settings=self._call_settings,
            pipeline=self._pipeline,
            ws_session=self._ws_for_stream,
        ):
            if ttfa_ms is not None:
                LOG.info(
                    "Fish TTS time-to-first-audio %.0fms sentence=%s",
                    ttfa_ms,
                    text[:80] + ("…" if len(text) > 80 else ""),
                )
                yield FishTtsFirstAudioFrame(ttfa_ms=ttfa_ms)
            if rate != _OUT_RATE:
                pcm = await self._resampler.resample(pcm, rate, _OUT_RATE)
            for i in range(0, len(pcm), _CHUNK_BYTES):
                chunk = pcm[i : i + _CHUNK_BYTES]
                if chunk:
                    if first_audio and ttfa_ms is None:
                        yield FishTtsFirstAudioFrame(ttfa_ms=0.0)
                    first_audio = False
                    yield TTSAudioRawFrame(
                        audio=chunk,
                        sample_rate=_OUT_RATE,
                        num_channels=1,
                        context_id=context_id,
                    )
