"""Privacy-related defaults: TypeSafe off, local language, optional spoken privacy line."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from fernruf.call_settings.store import update_call_settings
from fernruf.language import detect_call_language, prepare_call_settings
from fernruf.settings import Settings
from fernruf.voice.processors.opener import DisclosureOpenerProcessor
from pipecat.frames.frames import InputTransportMessageFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection


def test_typesafe_and_mood_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FERNRUF_TYPESAFE_ENABLED", raising=False)
    monkeypatch.delenv("FERNRUF_MOOD_ENABLED", raising=False)
    monkeypatch.delenv("FERNRUF_PRIVACY_MINIMAL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.typesafe_enabled is False
    assert settings.mood_enabled is False
    assert settings.typesafe_requests_enabled() is False


def test_privacy_minimal_forces_typesafe_off() -> None:
    settings = Settings(
        _env_file=None,
        privacy_minimal=True,
        typesafe_enabled=True,
        mood_enabled=True,
        typesafe_api_key="secret-token",
    )
    assert settings.typesafe_enabled is False
    assert settings.mood_enabled is False
    assert settings.typesafe_requests_enabled() is False


def test_local_language_without_typesafe(tmp_path: Path) -> None:
    def explode(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Jev must not be called when TypeSafe is disabled")

    settings = Settings(
        _env_file=None,
        typesafe_enabled=False,
        typesafe_api_key="unused",
        call_settings_db=str(tmp_path / "fernruf.sqlite"),
        llm_mock=True,
        voice_mock=True,
    )
    update_call_settings({"default_language": "de"}, settings=settings)
    detected = asyncio.run(
        detect_call_language(
            settings,
            "Please ask if Tuesday still works for the handover.",
            transport=httpx.MockTransport(explode),
        )
    )
    assert detected.source == "local"
    assert detected.language == "en"

    prepared = asyncio.run(
        prepare_call_settings(
            settings,
            "Bitte fragen Sie, ob der Termin am Dienstag passt.",
            transport=httpx.MockTransport(explode),
        )
    )
    assert prepared.call_language == "de"


def test_spoken_privacy_line_queues_second_tts() -> None:
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    opener = DisclosureOpenerProcessor(
        disclosure="Hallo, hier ist ein KI-Assistent.",
        privacy_line="Dieses Gespräch wird mit KI-Diensten verarbeitet.",
    )
    opener.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await opener.process_frame(
            InputTransportMessageFrame(message={"event": "MEDIA_START"}),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    speaks = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speaks) == 2
    assert speaks[1].text.startswith("Dieses Gespräch")
