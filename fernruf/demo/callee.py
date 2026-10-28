"""Second Pipecat bot: the practice that answers the local extension.

This side does not speak the Fernruf disclosure, check identity, or hang up.
It waits until it hears the caller, then answers in its own voice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI, WebSocket
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.workers.runner import WorkerRunner

from fernruf.call_settings.resolver import resolve_llm_endpoint_for_call
from fernruf.demo.persona import callee_system_prompt, resolve_callee_voice
from fernruf.llm.service_factory import create_fernruf_llm_service
from fernruf.settings import Settings
from fernruf.voice.asterisk_transport import FernrufAsteriskTransport
from fernruf.voice.embedded_uvicorn import (
    embedded_uvicorn_config,
    start_embedded_uvicorn,
    stop_embedded_uvicorn,
)
from fernruf.voice.services.faster_whisper_stt import FasterWhisperSTTService
from fernruf.voice.services.fish_rest_tts import FishRestTTSService
from fernruf.voice.transport_teardown import cancel_asterisk_output_tasks_async

LOG = logging.getLogger("fernruf.demo")


def settings_for_callee(settings: Settings) -> Settings:
    voice = resolve_callee_voice(settings.demo_callee_voice_id, settings.fish_voice_id)
    return settings.model_copy(update={"fish_voice_id": voice})


def callee_listen_uri(settings: Settings) -> str:
    host = settings.ws_listen_host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"ws://{host}:{settings.demo_callee_ws_port}/media"


def build_callee_pipeline(
    transport: FernrufAsteriskTransport,
    *,
    settings: Settings,
    persona: str,
    vad: SileroVADAnalyzer | None = None,
):
    system = callee_system_prompt(persona)
    stt = FasterWhisperSTTService(call_settings=settings)
    tts = FishRestTTSService(call_settings=settings)
    context = LLMContext(messages=[])
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad if vad is not None else SileroVADAnalyzer()
        ),
    )
    endpoint, _resolution = resolve_llm_endpoint_for_call(settings)
    llm = create_fernruf_llm_service(
        settings,
        model=endpoint.model,
        provider=endpoint.provider,
        system_instruction=system,
        session=None,
    )
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            pair.user(),
            llm,
            tts,
            pair.assistant(),
            transport.output(),
        ]
    )
    return pipeline, stt


async def run_callee_pipeline(
    websocket: WebSocket,
    *,
    settings: Settings,
    persona: str,
    vad: SileroVADAnalyzer | None = None,
) -> None:
    transport = FernrufAsteriskTransport(websocket=websocket)
    pipeline, stt = build_callee_pipeline(
        transport,
        settings=settings,
        persona=persona,
        vad=vad,
    )
    await stt.preload()
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            allow_interruptions=True,
        ),
    )
    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        await cancel_asterisk_output_tasks_async(transport)
        if not worker.has_finished():
            with contextlib.suppress(Exception):
                await worker.cancel()


async def serve_demo_callee(
    settings: Settings,
    *,
    persona: str,
    stop: asyncio.Event,
) -> None:
    voice_settings = settings_for_callee(settings)
    vad = SileroVADAnalyzer()
    app = FastAPI()

    @app.websocket("/media")
    async def media(websocket: WebSocket) -> None:
        await websocket.accept()
        LOG.info("demo callee answered")
        try:
            await run_callee_pipeline(
                websocket,
                settings=voice_settings,
                persona=persona,
                vad=vad,
            )
        except Exception:
            LOG.exception("demo callee pipeline failed")

    host = settings.ws_listen_host
    port = settings.demo_callee_ws_port
    config = embedded_uvicorn_config(app, host=host, port=port)
    server, task = start_embedded_uvicorn(config)
    LOG.info("demo callee listening on %s", callee_listen_uri(settings))
    try:
        await stop.wait()
    finally:
        await stop_embedded_uvicorn(server, task, timeout=8.0)
