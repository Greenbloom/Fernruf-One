from __future__ import annotations

import asyncio
import contextlib
import time

from fernruf.ari.teardown import ari_teardown_channels, shielded_ari_teardown


def test_teardown_order_pstn_bridge_ws() -> None:
    deleted: list[str] = []

    class FakeAri:
        def hangup(self, channel_id: str) -> None:
            deleted.append(f"/channels/{channel_id}")

        def destroy_bridge(self, bridge_id: str) -> None:
            deleted.append(f"/bridges/{bridge_id}")

    channels = {"pstn": "pstn-1", "ws": "ws-1", "bridge": "br-1"}
    pstn_done = False

    def mark_pstn() -> None:
        nonlocal pstn_done
        pstn_done = True

    asyncio.run(
        ari_teardown_channels(
            FakeAri(),  # type: ignore[arg-type]
            channels,
            reason="test",
            on_pstn_deleted=mark_pstn,
        )
    )
    assert deleted == ["/channels/pstn-1", "/bridges/br-1", "/channels/ws-1"]
    assert pstn_done is True
    assert channels["ws"] is None


def test_shielded_teardown_still_deletes_pstn_when_waiter_cancelled() -> None:
    deleted: list[str] = []

    class SlowPstnAri:
        def hangup(self, channel_id: str) -> None:
            deleted.append(channel_id)
            if channel_id == "pstn-1":
                time.sleep(0.15)

        def destroy_bridge(self, bridge_id: str) -> None:
            deleted.append(bridge_id)

    marked = {"pstn": False}

    async def run() -> None:
        task = asyncio.create_task(
            shielded_ari_teardown(
                SlowPstnAri(),  # type: ignore[arg-type]
                {"pstn": "pstn-1", "ws": "ws-1", "bridge": None},
                reason="cancel-test",
                on_pstn_deleted=lambda: marked.__setitem__("pstn", True),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.25)
        assert "pstn-1" in deleted
        assert marked["pstn"] is True

    asyncio.run(run())
