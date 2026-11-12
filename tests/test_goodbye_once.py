"""One farewell, and one Fish voice for that farewell.

The second live call spoke a garbled line and the end_call goodbye, and the
goodbye itself went out as two Fish requests.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from tests.test_call_phase_flows import _drive, _last_user, _pipeline, _start_script, _tool_end_call

from fernruf.call_settings.store import update_call_settings
from fernruf.settings import Settings
from fernruf.tts.fish_audio_cache import fish_cache_dir, fish_cache_key, write_fish_cache
from fernruf.tts.fish_rest_pipeline import FishRestPipeline
from fernruf.tts.rest_stream import stream_tts_sentences
from fernruf.voice.identity import scrub_identity_text
from fernruf.voice.tool_text import ToolTextGate, speakable_only

GARBLED = "Vielen Dank. Dann rufe ich jetzt  auf."
GOODBYE = "Vielen Dank für das Gespräch, dann meldet sich Erika Muster. Auf Wiederhören."


def test_lege_is_not_stripped_by_the_speakable_filters() -> None:
    """Nothing in the speak path deletes "lege". The gap is the text the model sent."""
    phrase = "Dann lege ich jetzt auf."
    for size in (1, 2, 3, 4):
        gate = ToolTextGate()
        parts: list[str] = []
        for i in range(0, len(phrase), size):
            parts.append(gate.feed(phrase[i : i + size]))
        parts.append(gate.finish())
        assert "".join(parts) == phrase
        assert gate.calls == []
    streamed = ToolTextGate()
    assert streamed.feed(GARBLED) + streamed.finish() == GARBLED
    assert speakable_only(phrase) == phrase
    assert "lege" in speakable_only("Jetzt lege ich auf.")
    scrubbed, changed = scrub_identity_text(phrase, principal="Erika Muster")
    assert changed is False
    assert scrubbed == phrase


def test_end_call_goodbye_is_the_only_farewell(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        if "Ja" in _last_user(body):
            chunks = [{"delta": {"content": GARBLED[i : i + 5]}} for i in range(0, len(GARBLED), 5)]
            chunks.extend(_tool_end_call("Fenster bestätigt", GOODBYE))
            return chunks
        return [{"delta": {"content": "Ist die Verbindung gut?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hallo.", "Ja."], settle_s=2.0))
    finally:
        server.shutdown()
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    assert any(GOODBYE in line for line in spoken)
    assert sum(GOODBYE in line for line in spoken) == 1
    assert not any("rufe ich jetzt" in line for line in spoken)
    assert not any(GARBLED in line for line in spoken)


def test_refused_end_call_still_speaks_the_reply(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    content = "Die Knöpfe gehen. Hören Sie mich noch?"

    def reply(_body: dict[str, Any]) -> list[dict[str, Any]]:
        chunks = [{"delta": {"content": content[i : i + 6]}} for i in range(0, len(content), 6)]
        chunks.extend(_tool_end_call("zu früh", "Tschüss!"))
        return chunks

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hallo."], settle_s=1.5))
    finally:
        server.shutdown()
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert hangup.await_count == 0
    assert session.phase.is_ending is False
    assert any("Die Knöpfe gehen" in line for line in spoken)
    assert any("Hören Sie mich noch" in line for line in spoken)
    assert not any("Tschüss" in line for line in spoken)


def test_farewell_is_one_fish_request_and_every_request_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests: list[dict[str, object]] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        requests.append(
            {
                "text": text,
                "model": kwargs["model"],
                "reference_id": kwargs.get("voice_id"),
                "latency": kwargs["settings"].fish_latency,
                "chunk_length": kwargs["settings"].fish_chunk_length,
            }
        )
        yield b"\x01\x02" * 3200, 1.0

    async def _warm(**kwargs):  # noqa: ANN003
        del kwargs

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    monkeypatch.setattr("fernruf.tts.rest_stream.astream_fish_tts_pcm_rest", _stream)
    monkeypatch.setattr("fernruf.voice.services.fish_rest_tts.warm_fish_rest_connection", _warm)

    cache_settings = Settings(
        _env_file=None,
        call_settings_db=str(tmp_path / "calls.sqlite"),
        fish_audio_model="drama-3",
        fish_voice_id="voice-test",
        fish_latency="balanced",
        call_language="de",
    )
    write_fish_cache(
        cache_settings,
        text="Auf Wiederhören.",
        voice_id="voice-test",
        model="drama-3",
        latency="balanced",
        pcm=b"\x03\x04" * 800,
        language="de",
    )

    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        if "Ja" in _last_user(body):
            return _tool_end_call("Fenster bestätigt", GOODBYE)
        return [{"delta": {"content": "Ist die Verbindung gut?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, _session, hangup = _pipeline(
        tmp_path, base, timeout_s=5.0, fallback="", fish_http=True
    )
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hallo.", "Ja."], settle_s=2.0))
    finally:
        server.shutdown()

    assert hangup.await_count == 1
    assert requests
    assert {row["model"] for row in requests} == {"drama-3"}
    assert {row["reference_id"] for row in requests} == {"voice-test"}
    assert {row["latency"] for row in requests} == {"balanced"}
    assert {row["chunk_length"] for row in requests} == {100}
    goodbye_rows = [row for row in requests if "Auf Wiederhören" in str(row["text"])]
    assert goodbye_rows == [
        {
            "text": GOODBYE,
            "model": "drama-3",
            "reference_id": "voice-test",
            "latency": "balanced",
            "chunk_length": 100,
        }
    ]
    assert not any(row["text"] == "Auf Wiederhören." for row in requests)


def test_cached_short_goodbye_is_not_half_of_a_longer_farewell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        fish_audio_api_key="test-key",
        fish_audio_model="drama-3",
        fish_voice_id="voice-test",
        fish_latency="balanced",
        fish_chunk_length=100,
        fish_first_clause=True,
        call_settings_db=str(tmp_path / "calls.sqlite"),
        call_language="de",
    )
    write_fish_cache(
        settings,
        text="Auf Wiederhören.",
        voice_id="voice-test",
        model="drama-3",
        latency="balanced",
        pcm=b"\x03\x04" * 800,
        language="de",
    )
    seen: list[str] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        del kwargs
        seen.append(text)
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    monkeypatch.setattr("fernruf.tts.rest_stream.astream_fish_tts_pcm_rest", _stream)
    pipeline = FishRestPipeline(
        settings=settings,
        api_key="test-key",
        model=settings.effective_fish_model(),
        voice_id="voice-test",
    )

    async def _run() -> None:
        async for _pcm, _rate, _ttfa in stream_tts_sentences(
            GOODBYE, settings=settings, pipeline=pipeline
        ):
            pass
        async for _pcm, _rate, _ttfa in stream_tts_sentences(
            "Das Fenster ist offen.", settings=settings, pipeline=pipeline
        ):
            pass

    asyncio.run(_run())
    assert seen == [GOODBYE, "Das Fenster ist offen."]


def _voice_settings(tmp_path: Path, *, fish_voice_id: str) -> Settings:
    return Settings(
        _env_file=None,
        fish_audio_api_key="test-key",
        fish_audio_model="drama-3",
        fish_voice_id=fish_voice_id,
        fish_latency="balanced",
        fish_chunk_length=100,
        fish_first_clause=True,
        call_settings_db=str(tmp_path / "calls.sqlite"),
        call_language="de",
    )


def test_goodbye_request_uses_the_call_voice_when_the_pipeline_snapshot_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _voice_settings(tmp_path, fish_voice_id="voice-configured")
    seen: list[str | None] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        del text
        seen.append(kwargs.get("voice_id"))
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    monkeypatch.setattr("fernruf.tts.rest_stream.astream_fish_tts_pcm_rest", _stream)
    pipeline = FishRestPipeline(
        settings=settings,
        api_key="test-key",
        model="drama-3",
        voice_id=None,
    )

    async def _run() -> None:
        async for _pcm, _rate, _ttfa in stream_tts_sentences(
            GOODBYE, settings=settings, pipeline=pipeline
        ):
            pass
        async for _pcm, _rate, _ttfa in stream_tts_sentences(
            "Eins zwei drei vier, der Rest bleibt dieselbe Stimme.",
            settings=settings,
            pipeline=pipeline,
        ):
            pass

    asyncio.run(_run())
    assert seen
    assert set(seen) == {"voice-configured"}


def test_cache_clip_without_a_voice_record_is_not_played(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _voice_settings(tmp_path, fish_voice_id="voice-configured")
    key = fish_cache_key(
        "Auf Wiederhören.",
        voice_id="voice-configured",
        model="drama-3",
        latency="balanced",
        language="de",
    )
    stale = b"\x0a\x0b" * 800
    (fish_cache_dir(settings) / f"{key}.pcm").write_bytes(stale)
    seen: list[str | None] = []
    heard = bytearray()

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        del text
        seen.append(kwargs.get("voice_id"))
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    monkeypatch.setattr("fernruf.tts.rest_stream.astream_fish_tts_pcm_rest", _stream)
    pipeline = FishRestPipeline(
        settings=settings,
        api_key="test-key",
        model="drama-3",
        voice_id=None,
    )

    async def _run() -> None:
        async for pcm, _rate, _ttfa in stream_tts_sentences(
            "Auf Wiederhören.", settings=settings, pipeline=pipeline
        ):
            heard.extend(pcm)

    asyncio.run(_run())
    assert stale not in bytes(heard)
    assert seen == ["voice-configured"]


def test_stored_voice_is_sent_when_the_env_voice_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _voice_settings(tmp_path, fish_voice_id="")
    update_call_settings({"voice_reference_id": "stored-voice"}, settings=settings)
    seen: list[str | None] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        del text
        seen.append(kwargs.get("voice_id"))
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    pipeline = FishRestPipeline(
        settings=settings,
        api_key="test-key",
        model="drama-3",
        voice_id=None,
    )

    async def _run() -> None:
        async for _pcm, _ttfa in pipeline.stream_text("Das Fenster ist offen."):
            pass

    asyncio.run(_run())
    assert seen == ["stored-voice"]
