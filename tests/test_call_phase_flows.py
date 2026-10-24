"""Scripted pipeline flows for the call-phase machine.

Fake STT is a user message, the LLM is a local SSE script, TTS is a wav fixture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import InputTransportMessageFrame, LLMMessagesAppendFrame, StartFrame
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.workers.runner import WorkerRunner
from pipecat_asterisk import AsteriskWebsocketTransport

from fernruf.settings import Settings
from fernruf.tts.audio import _fixture_pcm
from fernruf.tts.rest_stream import pcm_to_wav
from fernruf.voice.call_phase import farewell_in_text
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import build_m2_pipeline
from fernruf.voice.transport_teardown import cancel_asterisk_output_tasks_async
from tests.m2_pipeline_harness import (
    prime_transport_flow,
    unblock_asterisk_output,
    wait_until,
    wire_transport_bot_stopped_to_guard,
)

class _ScriptLLM(BaseHTTPRequestHandler):
    """SSE stand-in. ``reply`` maps the request body to chunks so a duplicate
    call for the same turn does not steal the next turn's script."""

    requests: list[dict[str, Any]] = []
    stall_s: float = 0.0
    stalls_left: int = 0
    reply: Any = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        _ScriptLLM.requests.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if _ScriptLLM.stalls_left > 0:
            _ScriptLLM.stalls_left -= 1
            ping = {"choices": [{"index": 0, "delta": {}}]}
            self.wfile.write(f"data: {json.dumps(ping)}\n\n".encode())
            self.wfile.flush()
            if _ScriptLLM.stall_s:
                import time

                time.sleep(_ScriptLLM.stall_s)
            return
        chunks = _ScriptLLM.reply(body) if _ScriptLLM.reply else [
            {"delta": {}, "finish_reason": "stop"}
        ]
        for chunk in chunks:
            payload = {
                "choices": [
                    {
                        "index": 0,
                        "delta": chunk.get("delta", {}),
                        "finish_reason": chunk.get("finish_reason"),
                    }
                ]
            }
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


def _tool_end_call(reason: str, goodbye: str) -> list[dict[str, Any]]:
    args = json.dumps({"reason": reason, "goodbye": goodbye}, ensure_ascii=False)
    return [
        {
            "delta": {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_end_phase",
                        "type": "function",
                        "function": {"name": "end_call", "arguments": ""},
                    }
                ]
            }
        },
        {
            "delta": {
                "tool_calls": [{"index": 0, "function": {"arguments": args}}]
            }
        },
        {"delta": {}, "finish_reason": "tool_calls"},
    ]


def _last_user(body: dict[str, Any]) -> str:
    users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
    if not users:
        return ""
    content = users[-1].get("content") or ""
    return content if isinstance(content, str) else str(content)


def _start_script(
    reply,
    *,
    stall_s: float = 0.0,
    stalls: int = 0,
) -> tuple[HTTPServer, str]:
    _ScriptLLM.reply = reply
    _ScriptLLM.requests = []
    _ScriptLLM.stall_s = stall_s
    _ScriptLLM.stalls_left = stalls
    server = HTTPServer(("127.0.0.1", 0), _ScriptLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


async def _drive(built, transport, lines: list[str], *, settle_s: float) -> None:
    params = PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(built.pipeline, params=params, cancel_timeout_secs=1.0)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await worker.queue_frame(StartFrame())
    await worker.queue_frame(
        InputTransportMessageFrame(
            message={"event": "MEDIA_START", "ptime": 20, "optimal_frame_size": 640}
        )
    )
    await prime_transport_flow(transport)
    user_agg = built.context_pair.user()
    for line in lines:
        before = len(_ScriptLLM.requests)
        await user_agg.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "user", "content": line}],
                run_llm=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await wait_until(lambda b=before: len(_ScriptLLM.requests) > b, timeout_s=8.0)
        await asyncio.sleep(0.4)
    await asyncio.sleep(settle_s)
    await _stop_pipeline(transport, worker, run_task)


def _pipeline(
    tmp_path: Path,
    base_url: str,
    *,
    timeout_s: float,
    fallback: str,
    task: str = "Kurzer Testanruf",
    call_language: str = "de",
):
    pcm = _fixture_pcm("phase", sample_rate=16000, duration_s=0.2)
    wav = tmp_path / "fish.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="test/model",
        llm_fallback_model=fallback,
        fish_tts_rest_fixture=str(wav),
        voice_mock=False,
        llm_mock=False,
        llm_filler_delay_s=30.0,
        llm_first_token_timeout_s=timeout_s,
        hangup_after_bot_stopped_margin_s=0.05,
        min_call_duration_before_hangup_s=0.0,
        disclosure_prefix="",
        call_language=call_language,
    )
    session = M2CallSession()
    hangup = AsyncMock()
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()
    transport = AsteriskWebsocketTransport(websocket=ws)
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session)
    built = build_m2_pipeline(
        transport,
        settings=settings,
        task=task,
        call_id="phase-flow",
        hooks=hooks,
        session=session,
    )
    unblock_asterisk_output(transport)
    wire_transport_bot_stopped_to_guard(transport, built.hangup, delay_s=0.15)
    return built, transport, session, hangup


async def _stop_pipeline(transport, worker, run_task) -> None:
    """Drop the Asterisk audio tasks, then cancel. A stuck CancelFrame must not
    hold the test for the worker's internal timeout."""
    try:
        await asyncio.wait_for(cancel_asterisk_output_tasks_async(transport), timeout=2.0)
    except TimeoutError:
        pass
    try:
        await asyncio.wait_for(worker.cancel(), timeout=3.0)
    except TimeoutError:
        pass
    try:
        await asyncio.wait_for(run_task, timeout=3.0)
    except TimeoutError:
        run_task.cancel()


def _tool_reruns() -> list[dict[str, Any]]:
    found = []
    for req in _ScriptLLM.requests:
        messages = req.get("messages") or []
        if any(isinstance(m, dict) and m.get("role") == "tool" for m in messages):
            found.append(req)
    return found


def _apology_count(caplog: pytest.LogCaptureFixture) -> int:
    return sum(1 for r in caplog.records if r.message.startswith("Playing LLM delay apology"))


def test_flow_answer_then_end_call_hangs_up_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        if "Ja" in _last_user(body):
            return _tool_end_call("Test ok", "Auf Wiederhören.")
        return [{"delta": {"content": "Ist die Verbindung gut?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hallo.", "Ja."], settle_s=2.0))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    assert _apology_count(caplog) == 0
    assert _tool_reruns() == []


def test_flow_plain_text_goodbye_hangs_up(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server, base = _start_script(
        lambda _body: [
            {"delta": {"content": "Vielen Dank. Auf Wiederhören."}, "finish_reason": "stop"}
        ]
    )
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Ja, alles klar."], settle_s=2.5))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    assert _apology_count(caplog) == 0
    assert _tool_reruns() == []


def test_flow_callee_goodbye_then_end_call_hangs_up(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server, base = _start_script(lambda _body: _tool_end_call("Gegenseite verabschiedet", ""))
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Auf Wiederhören"], settle_s=2.0))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    assert _tool_reruns() == []


def test_flow_end_call_after_question_is_blocked_without_rerun(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    chunks = [
        {"delta": {"content": "Hören Sie mich gut?"}},
        *_tool_end_call("zu früh", "Tschüss!"),
    ]
    server, base = _start_script(lambda _body: chunks)
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hallo."], settle_s=1.2))
    finally:
        server.shutdown()
    assert hangup.await_count == 0
    assert session.phase.is_ending is False
    assert _apology_count(caplog) == 0
    assert _tool_reruns() == []


def test_flow_empty_llm_stop_is_not_an_apology(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server, base = _start_script(
        lambda _body: [{"delta": {}, "finish_reason": "stop"}]
    )
    built, transport, _session, hangup = _pipeline(tmp_path, base, timeout_s=0.4, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Ja."], settle_s=0.8))
    finally:
        server.shutdown()
    assert _ScriptLLM.requests
    assert hangup.await_count == 0
    assert _apology_count(caplog) == 0
    assert any("nothing to say" in r.message for r in caplog.records)


def test_flow_real_stall_apologizes_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server, base = _start_script(lambda _body: [{"delta": {}, "finish_reason": "stop"}], stall_s=0.6, stalls=8)
    built, transport, _session, hangup = _pipeline(
        tmp_path, base, timeout_s=0.25, fallback="test/fallback"
    )
    caplog.set_level(logging.INFO)
    async def stall_two_turns() -> None:
        params = PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        worker = PipelineWorker(built.pipeline, params=params, cancel_timeout_secs=1.0)
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await worker.queue_frame(StartFrame())
        await worker.queue_frame(
            InputTransportMessageFrame(
                message={"event": "MEDIA_START", "ptime": 20, "optimal_frame_size": 640}
            )
        )
        await prime_transport_flow(transport)
        user_agg = built.context_pair.user()
        for line in ("Ja.", "Noch da?"):
            before = len(_ScriptLLM.requests)
            await user_agg.push_frame(
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": line}],
                    run_llm=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await wait_until(
                lambda b=before: len(_ScriptLLM.requests) >= b + 2,
                timeout_s=8.0,
            )
            await wait_until(
                lambda: _apology_count(caplog) >= 1,
                timeout_s=8.0,
            )
        await asyncio.sleep(0.4)
        await _stop_pipeline(transport, worker, run_task)

    try:
        asyncio.run(stall_two_turns())
    finally:
        server.shutdown()
    assert len(_ScriptLLM.requests) >= 2
    assert hangup.await_count == 0
    assert _apology_count(caplog) == 1


_ASK_TASK = "Frage nach, ob Alex Example bei Ihnen nochmal Updates machen muss."
_INDIRECT_ASK = (
    "Ich rufe im Auftrag von Alex Example an und wollte fragen, "
    "ob er bei Ihnen nochmal Updates machen muss."
)


def test_flow_indirect_question_blocks_end_call_until_answer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An information task is not done when the ask and end_call share a turn."""

    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        user = _last_user(body)
        if "muss" in user.lower():
            return _tool_end_call("Antwort erhalten", "Danke, auf Wiederhören.")
        return [
            {"delta": {"content": _INDIRECT_ASK}},
            *_tool_end_call("task_completed", "Auf Wiederhören."),
        ]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task=_ASK_TASK,
    )
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(
            _drive(
                built,
                transport,
                ["Ja und?", "Ja, das muss er nochmal machen."],
                settle_s=2.5,
            )
        )
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    # A blocked end_call must not start another completion before the callee answers.
    # The answer turn may still carry that tool result in history.
    reruns_before_answer = []
    for req in _ScriptLLM.requests:
        messages = req.get("messages") or []
        if not any(isinstance(m, dict) and m.get("role") == "tool" for m in messages):
            continue
        if "muss" not in _last_user(req).lower():
            reruns_before_answer.append(_last_user(req))
    assert reruns_before_answer == []
    assert len(_ScriptLLM.requests) >= 2
    blocked = [r for r in caplog.records if "end_call blocked" in r.message]
    assert blocked
    goodbyes = [r for r in caplog.records if r.message.startswith("end_call: speaking goodbye")]
    assert len(goodbyes) == 1
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert any("wollte fragen" in line for line in spoken)
    assert not any("end_call(" in line for line in spoken)
    prompt = ""
    for req in _ScriptLLM.requests:
        for message in req.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "system":
                prompt = str(message.get("content") or "")
    assert prompt
    assert _ASK_TASK in prompt
    assert "keine Erledigung" in prompt
    assert "nur für vage Aufgaben" in prompt


def test_flow_textual_end_call_is_spoken_as_goodbye_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``end_call(...)`` emitted as plain text is never spoken."""
    textual = "end_call(reason=call_ended, goodbye=Auf Wiederhören.)"
    server, base = _start_script(
        lambda _body: [{"delta": {"content": textual}, "finish_reason": "stop"}]
    )
    built, transport, session, hangup = _pipeline(tmp_path, base, timeout_s=5.0, fallback="")
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Tschüss."], settle_s=2.5))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert spoken
    assert not any("end_call(" in line or "reason=" in line for line in spoken)
    goodbye_tts = [line for line in spoken if "Auf Wiederhören" in line]
    assert len(goodbye_tts) == 1
    assert any("Parsed textual end_call" in r.message for r in caplog.records)
    assert _tool_reruns() == []


def test_confirmation_line_counts_as_one_farewell() -> None:
    german = "Vielen Dank, dann meldet sich Alex um 16 Uhr. Auf Wiederhören."
    english = "Thank you, then Alex will get in touch at 7 p.m. Goodbye."
    assert farewell_in_text(german, "Auf Wiederhören.")
    assert farewell_in_text(english, "Goodbye.")
    assert not farewell_in_text("Thank you.", "Goodbye.")


def test_flow_english_goodbye_is_spoken_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """LLM text 'Thank you. Goodbye.' plus end_call must not speak the farewell twice."""

    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        if "Tuesday" in _last_user(body):
            return [
                {"delta": {"content": "Thank you. Goodbye."}},
                *_tool_end_call("got it", "Thank you. Goodbye."),
            ]
        return [{"delta": {"content": "Does Tuesday still work?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task="Ask if Tuesday still works.",
        call_language="en",
    )
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hello?", "Yes, Tuesday works."], settle_s=2.5))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert sum("Goodbye" in line for line in spoken) == 1
    assert not any(r.message.startswith("end_call: speaking goodbye") for r in caplog.records)


def test_flow_german_confirmation_goodbye_is_spoken_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A farewell that confirms the time is still one goodbye, not a second line."""
    line = "Vielen Dank, dann meldet sich Alex um 16 Uhr. Auf Wiederhören."

    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        if "16" in _last_user(body):
            return [
                {"delta": {"content": line}},
                *_tool_end_call("got it", line),
            ]
        return [{"delta": {"content": "Passt Ihnen heute Abend ein Gespräch?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task="Frage, ob heute Abend Zeit für ein kurzes Gespräch ist und um wie viel Uhr.",
        call_language="de",
    )
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hallo?", "Ja, um 16 Uhr."], settle_s=2.5))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert sum("meldet sich Alex um 16 Uhr" in item for item in spoken) == 1
    assert sum("Auf Wiederhören" in item for item in spoken) == 1
    assert not any(r.message.startswith("end_call: speaking goodbye") for r in caplog.records)


def test_flow_english_confirmation_goodbye_is_spoken_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The English confirmation farewell is spoken once when end_call repeats it."""
    line = "Thank you, then Alex will get in touch at 7 p.m. Goodbye."

    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        if "7 p.m." in _last_user(body):
            return [
                {"delta": {"content": line}},
                *_tool_end_call("got it", line),
            ]
        return [{"delta": {"content": "Do you have time tonight, and at what time?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task="Ask whether they have time tonight for a short call with Alex, and at what time.",
        call_language="en",
    )
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(_drive(built, transport, ["Hello?", "Yes, 7 p.m. works."], settle_s=2.5))
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert sum("Alex will get in touch at 7 p.m." in item for item in spoken) == 1
    assert sum("Goodbye" in item for item in spoken) == 1
    assert not any(r.message.startswith("end_call: speaking goodbye") for r in caplog.records)


def test_flow_unclear_answer_asks_once_then_ends(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A wrong-language transcript is not an answer. Ask once, then a real answer may end."""

    def reply(body: dict[str, Any]) -> list[dict[str, Any]]:
        user = _last_user(body)
        if "Chemie" in user:
            return [
                {"delta": {"content": "Thank you. Goodbye."}},
                *_tool_end_call("done", "Thank you. Goodbye."),
            ]
        if "Tuesday" in user:
            return [
                {"delta": {"content": "Thank you. Goodbye."}},
                *_tool_end_call("got it", "Thank you. Goodbye."),
            ]
        return [{"delta": {"content": "Does Tuesday still work?"}, "finish_reason": "stop"}]

    server, base = _start_script(reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task="Ask if Tuesday still works.",
        call_language="en",
    )
    caplog.set_level(logging.INFO)
    try:
        asyncio.run(
            _drive(
                built,
                transport,
                ["Hello?", "Du hast eine Chemie.", "Yes, Tuesday works."],
                settle_s=2.5,
            )
        )
    finally:
        server.shutdown()
    assert hangup.await_count == 1
    assert session.phase.is_hung_up
    assert session.phase.reask_used
    spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert sum("didn't catch that" in line for line in spoken) == 1
    assert sum("Goodbye" in line for line in spoken) == 1
    assert any("Reasking once" in r.message for r in caplog.records)
