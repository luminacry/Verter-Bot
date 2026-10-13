"""Logging setup. Records are formatted and written by a background thread."""

from __future__ import annotations

import logging
import logging.handlers
import queue
import sys

from verter.core.log_ring import RING, RingHandler

logger = logging.getLogger("verter")

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> logging.handlers.QueueListener:
    """Route all logging through a queue so the event loop never blocks on log I/O.

    Args:
        level: Root log level name.

    Returns:
        The started listener; call ``stop()`` on shutdown to flush pending records.
    """
    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    listener = logging.handlers.QueueListener(
        records, stream, RingHandler(RING), respect_handler_level=True
    )
    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(records)]
    root.setLevel(level)
    listener.start()
    return listener
