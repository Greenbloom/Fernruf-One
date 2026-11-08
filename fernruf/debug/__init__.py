"""Local debug view. Off unless FERNRUF_DEBUG_UI_ENABLED is set."""

from fernruf.debug.trace import bind_call, configure_trace, current_trace, note, reset_call

__all__ = ["bind_call", "configure_trace", "current_trace", "note", "reset_call"]
