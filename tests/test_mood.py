"""Callee mood: Jev runs beside the reply and only the first Fish segment is tagged."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from fernruf.mood import (
    JEV_URL,
    MOOD_CRITERIA,
    MOOD_FISH_TAGS,
    MOOD_INSTRUCTIONS,
    JevMoodClient,
    MoodReading,
    MoodTurn,
    build_mood_request,
    fish_model_supports_inline_tags,
    reset_mood_notices,
)
from fernruf.settings import Settings
from fernruf.tts.fish_audio_cache import fish_cache_key
from fernruf.tts.fish_rest_pipeline import FishRestPipeline
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.processors.mood_turn import MoodTurnProcessor
from fernruf.voice.tool_text import ToolTextGate, speakable_only

ROOT = Path(__file__).resolve().parents[1]
MOOD_SOURCE = (ROOT / "fernruf" / "mood.py").read_text(encoding="utf-8") + (
    ROOT / "fernruf" / "voice" / "processors" / "mood_turn.py"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_notices() -> None:
    reset_mood_notices()


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "typesafe_enabled": True,
        "typesafe_api_key": "test-token-value",
        "mood_enabled": True,
        "mood_model": "jev-latest",
        "mood_timeout_ms": 300,
        "mood_min_confidence": 0.9,
        "fish_model": "s2.1-pro-free",
        "llm_model": "example/model-b",
        "voice_mock": False,
        "fish_tts_rest_fixture": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class _Scripted:
    def __init__(
        self,
        reading: MoodReading | None = None,
        *,
        delay_s: float = 0.0,
        error: BaseException | None = None,
    ) -> None:
        self.reading = reading or MoodReading("neutral", 0.0, latency_ms=1.0)
        self.delay_s = delay_s
        self.error = error
        self.calls: list[tuple[str, str | None]] = []

    async def detect(self, utterance: str, *, prior_bot: str | None = None) -> MoodReading:
        self.calls.append((utterance, prior_bot))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.reading


def _ready_turn(
    reading: MoodReading,
    settings: Settings | None = None,
    session: M2CallSession | None = None,
) -> MoodTurn:
    settings = settings or _settings()
    turn = MoodTurn(settings, session=session, client=_Scripted(reading))
    turn._reading = reading  # noqa: SLF001
    turn._gen = 1
    turn.begin_reply()
    return turn


def test_mood_enabled_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FERNRUF_MOOD_ENABLED", raising=False)
    monkeypatch.delenv("FERNRUF_TYPESAFE_ENABLED", raising=False)
    monkeypatch.delenv("FERNRUF_MOOD_MODEL", raising=False)
    monkeypatch.delenv("FERNRUF_MOOD_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("FERNRUF_MOOD_MIN_CONFIDENCE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.mood_enabled is False
    assert settings.typesafe_enabled is False
    assert settings.mood_model == "jev-latest"
    assert settings.mood_timeout_ms == 300
    assert settings.mood_min_confidence == 0.9


def test_no_chat_model_id_is_hardcoded() -> None:
    lowered = MOOD_SOURCE.lower()
    for needle in ("openai", "claude", "gpt-"):
        assert needle not in lowered
    assert "llm_model" not in MOOD_SOURCE
    body = build_mood_request(
        "Das ist schade.",
        "Wie geht es Ihnen?",
        model="jev-latest",
    )
    encoded = json.dumps(body).lower()
    assert body["model"] == "jev-latest"
    assert ("gr" + "ok") not in encoded
    assert body["questions"]["mood"]["type"] == "choice"
    assert body["questions"]["mood"]["instructions"] == MOOD_INSTRUCTIONS
    assert set(body["questions"]["mood"]["criteria"]) == set(MOOD_CRITERIA)


def test_fish_tag_mapping_and_model_gate() -> None:
    assert fish_model_supports_inline_tags("s2.1-pro-free")
    assert fish_model_supports_inline_tags("s2-pro")
    assert not fish_model_supports_inline_tags("s1")
    assert not fish_model_supports_inline_tags("speech-1.6")
    assert MOOD_FISH_TAGS["freundlich"] == "[warm]"
    assert MOOD_FISH_TAGS["belustigt"] == "[amused]"
    assert MOOD_FISH_TAGS["traurig"] == "[empathetic, soft voice]"
    assert MOOD_FISH_TAGS["genervt"] == "[calm]"
    assert MOOD_FISH_TAGS["misstrauisch"] == "[calm, reassuring]"
    assert MOOD_FISH_TAGS["gestresst"] == "[calm]"
    assert MOOD_FISH_TAGS["neutral"] == ""


def test_low_confidence_adds_no_tag() -> None:
    turn = _ready_turn(MoodReading("traurig", 0.89, latency_ms=70))
    assert turn.decorate_segment("Das tut mir leid.") == "Das tut mir leid."


def test_confidence_at_threshold_tags_first_segment_only() -> None:
    turn = _ready_turn(MoodReading("freundlich", 0.9, latency_ms=64))
    first = turn.decorate_segment("Guten Tag, ich rufe wegen des Termins an.")
    second = turn.decorate_segment("Passt der Dienstag bei Ihnen?")
    assert first == "[warm] Guten Tag, ich rufe wegen des Termins an."
    assert second == "Passt der Dienstag bei Ihnen?"


def test_notice_goodbye_filler_and_ending_turn_are_not_tagged() -> None:
    settings = _settings()
    session = M2CallSession()
    turn = _ready_turn(MoodReading("traurig", 1.0, latency_ms=50), settings, session)
    opener = settings.disclosure_prefix
    filler = settings.filler_phrase
    apology = settings.llm_delay_apology_phrase
    assert turn.decorate_segment(opener) == opener
    assert turn.decorate_segment(filler) == filler
    assert turn.decorate_segment(apology) == apology
    assert turn.decorate_segment("Auf Wiederhören!") == "Auf Wiederhören!"
    spoken = turn.decorate_segment("Das tut mir leid, das höre ich.")
    assert spoken.startswith("[empathetic, soft voice] ")

    ending = _ready_turn(MoodReading("traurig", 1.0, latency_ms=50), settings, session)
    session.phase.begin_ending(reason="end_call", goodbye="Auf Wiederhören!")
    assert (
        ending.decorate_segment("Gerne, ich lege jetzt auf und wünsche Ihnen einen schönen Tag.")
        == "Gerne, ich lege jetzt auf und wünsche Ihnen einen schönen Tag."
    )


def test_direct_speak_does_not_consume_the_reply_tag() -> None:
    turn = _ready_turn(MoodReading("belustigt", 1.0, latency_ms=40))
    turn.note_direct_speak()
    assert turn.decorate_segment("Einen Moment…") == "Einen Moment…"
    turn.clear_direct_speak()
    assert turn.decorate_segment("Das ist ja ein schöner Zufall heute.") == (
        "[amused] Das ist ja ein schöner Zufall heute."
    )


def test_transcript_and_cache_key_stay_untagged() -> None:
    settings = _settings()
    spoken = "Das tut mir leid, das höre ich."
    turn = _ready_turn(MoodReading("traurig", 1.0, latency_ms=72), settings)
    fish_text = turn.decorate_segment(spoken)
    assert fish_text.startswith("[empathetic, soft voice] ")
    assert "[" not in spoken
    gate = ToolTextGate()
    assert speakable_only(fish_text) == fish_text
    assert gate.feed(spoken) == spoken
    plain_key = fish_cache_key(
        settings.disclosure_prefix,
        voice_id="voice",
        model="s2.1-pro-free",
        latency="balanced",
    )
    assert turn.decorate_segment(settings.disclosure_prefix) == settings.disclosure_prefix
    assert (
        fish_cache_key(
            settings.disclosure_prefix,
            voice_id="voice",
            model="s2.1-pro-free",
            latency="balanced",
        )
        == plain_key
    )


def test_disabled_or_unsupported_model_or_missing_key_stays_neutral() -> None:
    disabled = MoodTurn(_settings(mood_enabled=False), client=_Scripted(MoodReading("traurig", 1)))
    disabled.start("Das ist schade.")
    disabled.begin_reply()
    assert disabled.decorate_segment("Das tut mir leid.") == "Das tut mir leid."

    unsupported_client = _Scripted(MoodReading("traurig", 1))
    unsupported = MoodTurn(
        _settings(fish_model="s1", typesafe_api_key="test-token-value"),
        client=unsupported_client,
    )
    unsupported.start("Das ist schade.")
    unsupported.begin_reply()
    assert unsupported_client.calls == []
    assert unsupported.decorate_segment("Das tut mir leid.") == "Das tut mir leid."

    missing_client = _Scripted(MoodReading("traurig", 1))
    missing = MoodTurn(_settings(typesafe_api_key=""), client=missing_client)
    missing.start("Das ist schade.")
    assert missing_client.calls == []
    missing.begin_reply()
    assert missing.decorate_segment("Das tut mir leid.") == "Das tut mir leid."


def test_timeout_does_not_block_tts_and_late_score_is_ignored() -> None:
    client = _Scripted(MoodReading("traurig", 1.0, latency_ms=1800), delay_s=1.0)
    turn = MoodTurn(_settings(mood_timeout_ms=2000), client=client)

    async def _run() -> tuple[str, float, str]:
        turn.start("Ich bin heute richtig traurig.")
        started = time.perf_counter()
        turn.begin_reply()
        first = turn.decorate_segment("Das tut mir leid.")
        elapsed = time.perf_counter() - started
        await asyncio.sleep(1.05)
        second = turn.decorate_segment("Wir finden eine Lösung.")
        return first, elapsed, second

    first, elapsed, second = asyncio.run(_run())
    assert first == "Das tut mir leid."
    assert second == "Wir finden eine Lösung."
    assert elapsed < 0.1
    assert client.calls


def test_finished_score_tags_only_the_first_fish_clause(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        del kwargs
        calls.append(text)
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    settings = _settings(fish_first_clause=True)
    client = _Scripted(MoodReading("traurig", 1.0, latency_ms=72))
    turn = MoodTurn(settings, client=client)

    async def _run() -> None:
        turn.start("Ach, das ist wirklich schade.")
        await turn._task  # noqa: SLF001
        turn.begin_reply()
        pipeline = FishRestPipeline(
            settings=settings,
            api_key="k",
            model=settings.effective_fish_model(),
            voice_id=None,
            prepare_segment=turn.decorate_segment,
        )
        async for _pcm, _ttfa in pipeline.stream_text(
            "Eins zwei drei vier, die Verbindung steht und klingt gut."
        ):
            pass
        async for _pcm, _ttfa in pipeline.stream_text(
            "Und hier kommt noch ein zweiter Satz dazu."
        ):
            pass

    asyncio.run(_run())
    assert calls[0] == "[empathetic, soft voice] Eins zwei drei vier,"
    assert calls[1] == "die Verbindung steht und klingt gut."
    assert all(not item.startswith("[") for item in calls[1:])
    assert client.calls[0][0] == "Ach, das ist wirklich schade."


def test_tts_log_and_synth_log_keep_the_spoken_text(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from fernruf.voice.services.fish_rest_tts import FishRestTTSService

    calls: list[str] = []

    async def _stream(text: str, **kwargs):  # noqa: ANN003
        del kwargs
        calls.append(text)
        yield b"\x01\x02" * 20, 1.0

    monkeypatch.setattr("fernruf.tts.fish_rest_pipeline.astream_fish_tts_pcm_rest", _stream)
    settings = _settings(fish_audio_api_key="fish-test", fish_first_clause=False)
    synth: list[str] = []
    client = _Scripted(MoodReading("freundlich", 1.0, latency_ms=55))
    turn = MoodTurn(settings, client=client)
    service = FishRestTTSService(call_settings=settings, synth_log=synth, mood=turn)

    async def _run() -> None:
        turn.start("Ja, super!")
        await turn._task  # noqa: SLF001
        turn.begin_reply()
        async for _frame in service.run_tts("Das freut mich sehr.", "ctx"):
            pass

    caplog.set_level("INFO")
    asyncio.run(_run())
    assert calls == ["[warm] Das freut mich sehr."]
    assert synth == ["Das freut mich sehr."]
    tts_logs = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert tts_logs == ["TTS start: Das freut mich sehr."]
    mood_logs = [r.message for r in caplog.records if r.message.startswith("mood=")]
    assert any("mood=freundlich" in line and "tag=[warm]" in line for line in mood_logs)
    assert all("[" not in line or line.startswith("mood=") for line in tts_logs)


def test_jev_timeout_and_low_level_request_shape() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"answers": {"mood": {"choice": "misstrauisch", "confidence": 1}}},
        )

    settings = _settings(llm_model="example/model-b")

    async def _ok() -> MoodReading:
        client = JevMoodClient(settings, transport=httpx.MockTransport(handler))
        return await client.detect("Warum rufen Sie an?", prior_bot="Guten Tag.")

    reading = asyncio.run(_ok())
    assert seen["url"] == JEV_URL
    assert seen["auth"] == "Bearer test-token-value"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "jev-latest"
    assert ("gr" + "ok") not in json.dumps(body).lower()
    assert "Guten Tag." in body["state"]
    assert "Warum rufen Sie an?" in body["state"]
    assert reading.choice == "misstrauisch"
    assert reading.confidence == 1.0

    async def slow(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(0.2)
        return httpx.Response(
            200,
            json={"answers": {"mood": {"choice": "traurig", "confidence": 1}}},
        )

    async def _slow() -> MoodReading:
        client = JevMoodClient(
            _settings(mood_timeout_ms=50),
            transport=httpx.MockTransport(slow),
        )
        return await client.detect("Das ist schade.")

    timed = asyncio.run(_slow())
    assert timed.choice == "neutral"
    assert timed.timed_out is True
    assert timed.confidence == 0.0


def test_rejected_key_is_neutral_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": "nope"})

    settings = _settings()

    async def _twice() -> list[MoodReading]:
        client = JevMoodClient(settings, transport=httpx.MockTransport(handler))
        first = await client.detect("Hallo.")
        second = await client.detect("Noch einmal.")
        return [first, second]

    caplog.set_level("WARNING", logger="fernruf.mood")
    first, second = asyncio.run(_twice())
    assert first.choice == "neutral"
    assert second.choice == "neutral"
    warnings = [r.message for r in caplog.records if "rejected" in r.message]
    assert len(warnings) == 1
    assert "test-token-value" not in " ".join(warnings)


def test_processor_starts_on_final_transcript_without_blocking() -> None:
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    client = _Scripted(MoodReading("neutral", 1.0), delay_s=0.5)
    session = M2CallSession()
    session.context_messages = [
        {"role": "assistant", "content": "Wie geht es Ihnen?"},
    ]
    turn = MoodTurn(_settings(mood_timeout_ms=2000), session=session, client=client)
    processor = MoodTurnProcessor(turn, session=session)
    forwarded: list[object] = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        del direction
        forwarded.append(frame)

    processor.push_frame = capture  # type: ignore[method-assign]

    async def _run() -> float:
        started = time.perf_counter()
        await processor.process_frame(
            TranscriptionFrame(text="Mir geht es nicht so gut.", user_id="u", timestamp="t"),
            FrameDirection.DOWNSTREAM,
        )
        elapsed = time.perf_counter() - started
        await asyncio.sleep(0)
        task = turn._task  # noqa: SLF001
        assert task is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return elapsed

    elapsed = asyncio.run(_run())
    assert elapsed < 0.1
    assert len(forwarded) == 1
    assert client.calls == [("Mir geht es nicht so gut.", "Wie geht es Ihnen?")]
