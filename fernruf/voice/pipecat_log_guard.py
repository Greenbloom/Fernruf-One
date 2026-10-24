from __future__ import annotations

import logging
from dataclasses import dataclass, field


@dataclass
class PipecatLogGuard:
    """Count pipecat ERROR lines and unusable-processor messages during a run."""

    error_lines: list[str] = field(default_factory=list)
    _loguru_sink_id: int | None = field(default=None, repr=False)
    _installed: bool = field(default=False, repr=False)

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        logging.getLogger("pipecat").addHandler(self._logging_handler)
        try:
            from loguru import logger

            self._loguru_sink_id = logger.add(
                self._loguru_sink,
                level="ERROR",
                filter=lambda record: "pipecat" in record["name"],
            )
        except ImportError:
            self._loguru_sink_id = None

    def remove(self) -> None:
        if not self._installed:
            return
        self._installed = False
        logging.getLogger("pipecat").removeHandler(self._logging_handler)
        if self._loguru_sink_id is not None:
            try:
                from loguru import logger

                logger.remove(self._loguru_sink_id)
            except (ImportError, ValueError):
                pass
            self._loguru_sink_id = None

    @property
    def count(self) -> int:
        return len(self.error_lines)

    def _record(self, msg: str) -> None:
        self.error_lines.append(msg)
        if "can no longer do its job" in msg:
            self.error_lines.append("processor_unusable")

    def _logging_handler(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        self._record(record.getMessage())

    def _loguru_sink(self, message) -> None:
        record = message.record
        self._record(record["message"])
