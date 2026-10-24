from __future__ import annotations

import asyncio
from pathlib import Path

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from fernruf.settings import Settings
from fernruf.tts.rest_stream import pcm_to_wav
from fernruf.tts.audio import _fixture_pcm
from fernruf.voice.processors.filler import LlmFillerProcessor
from fernruf.voice.services.fish_rest_tts import FishRestTTSService


def test_fish_tts_synthesizes_both_filler_and_llm_reply(tmp_path: Path) -> None:
    """After filler fix, both filler phrase and LLM reply must reach FishRestTTSService."""
    pcm = _fixture_pcm("reply", sample_rate=16000, duration_s=0.5)
    wav = tmp_path / "fish.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))
    settings = Settings(_env_file=None, fish_tts_rest_fixture=str(wav))
    synth_log: list[str] = []
    tts = FishRestTTSService(call_settings=settings, synth_log=synth_log)
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.15)

    async def collect_tts(text: str) -> None:
        async for _frame in tts.run_tts(text, context_id="test"):
            pass

    async def run() -> None:
        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.2)
        await collect_tts("Einen Moment…")
        await filler.process_frame(
            LLMTextFrame(text="Ja, ich höre Sie gut."),
            FrameDirection.DOWNSTREAM,
        )
        await filler.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await collect_tts("Ja, ich höre Sie gut.")

    asyncio.run(run())
    combined = " ".join(synth_log)
    assert "Einen Moment" in combined
    assert "Ja, ich höre Sie gut" in combined


def test_pipeline_order_tts_before_assistant_aggregator() -> None:
    from fernruf.voice import m2_pipeline
    import inspect

    src = inspect.getsource(m2_pipeline.build_m2_pipeline)
    assert "filler_gate," in src
    assert src.index("tts,") < src.index("pair.assistant()")


def test_pipeline_filler_gate_after_llm() -> None:
    from fernruf.voice import m2_pipeline
    import inspect

    src = inspect.getsource(m2_pipeline.build_m2_pipeline)
    assert src.index("llm,") < src.index("filler_gate,") < src.index("tts,")
