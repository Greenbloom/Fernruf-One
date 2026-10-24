from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService

from fernruf.settings import Settings
from fernruf.stt.whisper import (
    log_live_stt_kwargs,
    preload_whisper_model,
    transcribe_pcm,
    whisper_language_code,
)
from fernruf.voice.processors.verify_flow import VerifyFlowState

LOG = logging.getLogger("fernruf.voice.stt")


class FasterWhisperSTTService(SegmentedSTTService):
    """Segmented faster-whisper STT (16 kHz slin16 from chan_websocket).

    The parent service transcribes only audio between VAD speech start and
    stop. Segments the VAD never marks are not sent to Whisper.
    """

    def __init__(
        self,
        *,
        call_settings: Settings,
        mock_text: str | None = None,
        verify_state: VerifyFlowState | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            sample_rate=16000,
            settings=STTSettings(
                model=call_settings.whisper_model,
                language=whisper_language_code(call_settings),
            ),
            **kwargs,
        )
        self._call_settings = call_settings
        log_live_stt_kwargs(call_settings)
        self._mock_text = mock_text
        self._verify_state = verify_state
        self._mock_inbound = 0
        self._mock_segment_sent = False

    @property
    def wants_wav_segments(self) -> bool:
        return False

    async def preload(self) -> None:
        if self._mock_text is not None:
            return
        await asyncio.to_thread(preload_whisper_model, self._call_settings)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            self._mock_text
            and not self._mock_segment_sent
            and isinstance(frame, InputAudioRawFrame)
        ):
            self._mock_inbound += len(frame.audio or b"")
            if self._mock_inbound >= 8000:
                self._mock_segment_sent = True
                await self._handle_user_stopped_speaking(VADUserStoppedSpeakingFrame())

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if self._mock_text is not None:
            if len(audio) < 3200:
                return
            text = self._mock_text
        else:
            seg = await asyncio.to_thread(
                transcribe_pcm,
                audio,
                sample_rate=self.sample_rate,
                settings=self._call_settings,
            )
            text = seg.text.strip()
        if not text:
            return
        LOG.info("STT final: %s", text)
        if self._verify_state is not None:
            self._verify_state.checks["callee_transcribed"] = True
            self._verify_state.transcript.append(
                {"speaker": "callee", "text": text},
            )
        ts = datetime.now(timezone.utc).isoformat()
        yield TranscriptionFrame(text=text, user_id=self._user_id, timestamp=ts)
