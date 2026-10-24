from __future__ import annotations

import asyncio
import logging
from typing import Callable

from fernruf.ari.client import AriSession

LOG = logging.getLogger("fernruf.ari.teardown")


async def ari_teardown_channels(
    ari: AriSession,
    channels: dict[str, str | None],
    *,
    reason: str,
    skip_pstn: bool = False,
    on_pstn_deleted: Callable[[], None] | None = None,
) -> None:
    """Hang up PSTN first, then bridge, then WS (WS last so the pipeline can finish PSTN)."""
    LOG.info("ARI teardown (%s)", reason)
    if not skip_pstn:
        pstn = channels.get("pstn")
        if pstn:
            await asyncio.to_thread(ari.hangup, pstn)
            if on_pstn_deleted is not None:
                on_pstn_deleted()
    bridge = channels.get("bridge")
    if bridge:
        await asyncio.to_thread(ari.destroy_bridge, bridge)
        channels["bridge"] = None
    ws = channels.get("ws")
    if ws:
        await asyncio.to_thread(ari.hangup, ws)
        channels["ws"] = None


async def shielded_ari_teardown(
    ari: AriSession,
    channels: dict[str, str | None],
    *,
    reason: str,
    skip_pstn: bool = False,
    on_pstn_deleted: Callable[[], None] | None = None,
) -> None:
    await asyncio.shield(
        ari_teardown_channels(
            ari,
            channels,
            reason=reason,
            skip_pstn=skip_pstn,
            on_pstn_deleted=on_pstn_deleted,
        )
    )
