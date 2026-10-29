from __future__ import annotations

import asyncio

from pipecat.frames.frames import InputTransportMessageFrame, LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.processors.opener import DisclosureOpenerProcessor


def test_opener_single_context_append_and_tts_not_duplicating() -> None:
    disclosure = "Hallo, hier ist ein KI-Assistent."
    opener = DisclosureOpenerProcessor(disclosure=disclosure)
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    opener.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await opener.process_frame(
            InputTransportMessageFrame(message={"event": "MEDIA_START"}),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    appends = [f for f in pushed if isinstance(f, LLMMessagesAppendFrame)]
    speaks = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(appends) == 1
    assert appends[0].messages[0]["content"] == disclosure
    assert len(speaks) == 1
    assert speaks[0].append_to_context is False


def test_opener_privacy_line_is_spoken_but_not_in_llm_context() -> None:
    disclosure = "Hallo, hier ist ein KI-Assistent."
    privacy = "This call uses external AI services."
    opener = DisclosureOpenerProcessor(disclosure=disclosure, privacy_line=privacy)
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    opener.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await opener.process_frame(
            InputTransportMessageFrame(message={"event": "MEDIA_START"}),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    speaks = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    appends = [f for f in pushed if isinstance(f, LLMMessagesAppendFrame)]
    assert len(speaks) == 2
    assert speaks[1].text == privacy
    assert len(appends) == 1
    assert appends[0].messages[0]["content"] == disclosure


def test_opener_speaks_the_notice_and_the_request_together() -> None:
    disclosure = "Hallo, hier ist ein KI-Assistent."
    request = "Passt Dienstag für einen Termin?"
    opener = DisclosureOpenerProcessor(disclosure=disclosure, request=request)
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    opener.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await opener.process_frame(
            InputTransportMessageFrame(message={"event": "MEDIA_START"}),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    speaks = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    appends = [f for f in pushed if isinstance(f, LLMMessagesAppendFrame)]
    spoken = f"{disclosure} {request}"
    assert len(speaks) == 1
    assert speaks[0].text == spoken
    assert len(appends) == 1
    assert appends[0].messages[0]["content"] == spoken
