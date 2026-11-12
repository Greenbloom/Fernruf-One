"""Localhost call monitor. Observes a call. Does not control it."""

from fernruf.monitor.hub import CallMonitor
from fernruf.monitor.state import CallLifecycle

__all__ = ["CallLifecycle", "CallMonitor"]
