from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from fernruf.voice.call_session import M2CallSession

SendDtmfFn = Callable[[str], Awaitable[None]]
HangupFn = Callable[[], Awaitable[None]]
RequestPipelineShutdownFn = Callable[[], Awaitable[None]]
ScheduleHangupFn = Callable[[], Awaitable[None]]


@dataclass
class M2CallHooks:
    send_dtmf: SendDtmfFn
    hangup: HangupFn
    session: M2CallSession | None = field(default=None)
    request_pipeline_shutdown: RequestPipelineShutdownFn | None = field(default=None)
    schedule_hangup: ScheduleHangupFn | None = field(default=None)
