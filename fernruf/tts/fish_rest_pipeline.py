from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from fernruf.settings import Settings
from fernruf.tts.fish_clause import split_first_clause
from fernruf.tts.fish_rest_client import astream_fish_tts_pcm_rest

LOG = logging.getLogger("fernruf.tts.fish_pipeline")


async def _collect_stream(
    text: str,
    *,
    settings: Settings,
    api_key: str,
    model: str,
    voice_id: str | None,
    rest_fixture_path: str | None,
    cancel: asyncio.Event,
) -> list[tuple[bytes, float | None]]:
    chunks: list[tuple[bytes, float | None]] = []
    async for pcm, ttfa in astream_fish_tts_pcm_rest(
        text,
        settings=settings,
        api_key=api_key,
        model=model,
        voice_id=voice_id,
        rest_fixture_path=rest_fixture_path,
    ):
        if cancel.is_set():
            break
        chunks.append((pcm, ttfa))
    return chunks


@dataclass
class FishRestPipeline:
    """Ordered Fish REST synthesis; prefetch segment N+1 while playing N (max one prefetch)."""

    settings: Settings
    api_key: str
    model: str
    voice_id: str | None
    rest_fixture_path: str | None = None
    prepare_segment: Callable[[str], str] | None = None
    _cancel: asyncio.Event = field(default_factory=asyncio.Event)

    def _api_text(self, segment: str) -> str:
        if self.prepare_segment is None:
            return segment
        return self.prepare_segment(segment)

    def cancel(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    def _segments_for_text(self, text: str) -> list[str]:
        if self.settings.fish_first_clause:
            first, rest = split_first_clause(text)
            if first:
                return [first, rest]
        return [text.strip()] if text.strip() else []

    async def stream_text(
        self,
        text: str,
        *,
        on_ttfa: callable | None = None,
    ) -> AsyncIterator[tuple[bytes, float | None]]:
        segments = self._segments_for_text(text)
        if not segments:
            return
        prefetch: asyncio.Task[list[tuple[bytes, float | None]]] | None = None
        for idx, segment in enumerate(segments):
            if self._cancel.is_set():
                LOG.info("Fish REST pipeline cancelled before segment %s", idx)
                if prefetch and not prefetch.done():
                    prefetch.cancel()
                return
            if prefetch is not None:
                try:
                    chunks = await prefetch
                except asyncio.CancelledError:
                    return
                for pcm, ttfa in chunks:
                    if ttfa is not None and on_ttfa:
                        on_ttfa(ttfa)
                    yield pcm, ttfa
                prefetch = None
            elif idx == 0:
                async for pcm, ttfa in astream_fish_tts_pcm_rest(
                    self._api_text(segment),
                    settings=self.settings,
                    api_key=self.api_key,
                    model=self.model,
                    voice_id=self.voice_id,
                    rest_fixture_path=self.rest_fixture_path,
                ):
                    if self._cancel.is_set():
                        return
                    if ttfa is not None and on_ttfa:
                        on_ttfa(ttfa)
                    yield pcm, ttfa
            if idx + 1 < len(segments) and not self._cancel.is_set():
                nxt = self._api_text(segments[idx + 1])
                prefetch = asyncio.create_task(
                    _collect_stream(
                        nxt,
                        settings=self.settings,
                        api_key=self.api_key,
                        model=self.model,
                        voice_id=self.voice_id,
                        rest_fixture_path=self.rest_fixture_path,
                        cancel=self._cancel,
                    )
                )
        if prefetch is not None and not self._cancel.is_set():
            try:
                chunks = await prefetch
            except asyncio.CancelledError:
                return
            for pcm, ttfa in chunks:
                if ttfa is not None and on_ttfa:
                    on_ttfa(ttfa)
                yield pcm, ttfa
