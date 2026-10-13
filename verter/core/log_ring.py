"""In-memory ring of recent log records for the dashboard."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

_CAPACITY = 500


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: str
    level: str
    logger: str
    message: str


class LogRing:
    """Bounded buffer. Appends happen on the logging thread; reads take a snapshot."""

    def __init__(self, capacity: int = _CAPACITY) -> None:
        self._items: deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def append(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.exc_info is not None and record.exc_info[0] is not None:
            message = f"{message} ({record.exc_info[0].__name__})"
        entry = LogEntry(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            level=record.levelname,
            logger=record.name,
            message=message,
        )
        with self._lock:
            self._items.append(entry)

    def snapshot(self, limit: int) -> list[LogEntry]:
        with self._lock:
            items = list(self._items)
        if limit < len(items):
            return items[-limit:]
        return items

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class RingHandler(logging.Handler):
    """Copies records into a :class:`LogRing`. Never raises into the logging system."""

    def __init__(self, ring: LogRing) -> None:
        super().__init__()
        self.ring = ring

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.ring.append(record)
        except Exception:  # noqa: BLE001 - logging handlers must not raise
            self.handleError(record)


RING = LogRing()
