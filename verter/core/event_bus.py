"""Bounded event queue drained by a fixed pool of workers.

``commit()`` never blocks: when ``max_pending`` events are waiting, new events
are dropped and counted instead of growing memory without bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from verter.core.log import logger
from verter.core.platform.message_event import MessageEvent
from verter.core.runtime_state import RuntimeHandle, current_runtime

type SessionInterceptor = Callable[[MessageEvent], bool]
"""Receives events for one conversation before they are queued; True consumes the event."""

_DROP_LOG_INTERVAL_S = 10.0


class EventProcessor(Protocol):
    async def execute(self, event: MessageEvent) -> None: ...


@dataclass(slots=True)
class EventBusStats:
    received: int = 0
    intercepted: int = 0
    dropped: int = 0
    processed: int = 0
    failed: int = 0
    timed_out: int = 0


class EventBus:
    def __init__(
        self,
        processor: EventProcessor,
        *,
        max_pending: int = 10_000,
        workers: int = 64,
        event_timeout_s: float = 300.0,
    ) -> None:
        self._processor = processor
        self._queue: asyncio.Queue[MessageEvent] = asyncio.Queue(maxsize=max_pending)
        self._worker_count = workers
        self._timeout = event_timeout_s or None
        self._workers: list[asyncio.Task[None]] = []
        self._interceptors: dict[str, SessionInterceptor] = {}
        self._accepting = False
        self._in_flight = 0
        self._last_drop_log = 0.0
        self.stats = EventBusStats()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def commit(self, event: MessageEvent) -> bool:
        """Hand ``event`` to an interceptor or queue it; returns False if it was dropped."""
        stats = self.stats
        stats.received += 1
        if self._interceptors:
            interceptor = self._interceptors.get(event.unified_msg_origin)
            if interceptor is not None and interceptor(event):
                stats.intercepted += 1
                return True
        if not self._accepting:
            stats.dropped += 1
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            stats.dropped += 1
            now = time.monotonic()
            if now - self._last_drop_log >= _DROP_LOG_INTERVAL_S:
                self._last_drop_log = now
                logger.warning(
                    "Event queue full (%d pending); dropping events (%d dropped so far)",
                    self._queue.maxsize,
                    stats.dropped,
                )
            return False
        return True

    def set_interceptor(self, origin: str, interceptor: SessionInterceptor) -> None:
        self._interceptors[origin] = interceptor

    def remove_interceptor(self, origin: str, interceptor: SessionInterceptor) -> None:
        if self._interceptors.get(origin) is interceptor:
            del self._interceptors[origin]

    async def start(self) -> None:
        self._accepting = True
        # Workers copy the current context. The handle is mutable, so the platform
        # manager can be attached after the workers have already started.
        handle = current_runtime.get()
        if handle is None:
            handle = RuntimeHandle()
            current_runtime.set(handle)
        handle.bus = self
        self._workers = [
            asyncio.create_task(self._work(), name=f"event-worker-{i}")
            for i in range(self._worker_count)
        ]

    async def wait_idle(self) -> None:
        """Wait until every queued event has been processed."""
        await self._queue.join()

    async def stop(self, *, drain_timeout_s: float = 5.0) -> None:
        """Stop accepting events, finish queued ones within the timeout, then cancel workers."""
        self._accepting = False
        try:
            await asyncio.wait_for(self._queue.join(), drain_timeout_s)
        except TimeoutError:
            logger.warning("Dropping %d unprocessed events at shutdown", self._queue.qsize())
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    async def _work(self) -> None:
        queue = self._queue
        stats = self.stats
        while True:
            event = await queue.get()
            self._in_flight += 1
            deadline = asyncio.timeout(self._timeout)
            try:
                async with deadline:
                    await self._processor.execute(event)
                stats.processed += 1
            except Exception as exc:  # noqa: BLE001 - one bad event must not stop the worker
                if isinstance(exc, TimeoutError) and deadline.expired():
                    stats.timed_out += 1
                    logger.warning(
                        "Event from %s exceeded %ss and was cancelled",
                        event.unified_msg_origin,
                        self._timeout,
                    )
                else:
                    stats.failed += 1
                    logger.exception(
                        "Pipeline failed for an event from %s", event.unified_msg_origin
                    )
            finally:
                self._in_flight -= 1
                queue.task_done()
