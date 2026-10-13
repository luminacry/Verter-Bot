import asyncio
import logging

import pytest

from tests.helpers import make_inmemory
from verter.core.event_bus import EventBus
from verter.core.platform.message_event import MessageEvent


def event(text: str = "x", sender: str = "user") -> MessageEvent:
    return make_inmemory().build_event(text, sender_id=sender)


class Recording:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = False
        self.error: BaseException | None = None

    async def execute(self, event: MessageEvent) -> None:
        self.events.append(event)
        self.started.set()
        if self.error is not None:
            raise self.error
        if self.hold:
            await self.release.wait()


async def test_workers_process_committed_events() -> None:
    processor = Recording()
    bus = EventBus(processor, workers=2, event_timeout_s=0)
    await bus.start()
    first, second = event("a"), event("b", sender="other")
    assert bus.commit(first)
    assert bus.commit(second)
    await bus.wait_idle()
    assert processor.events == [first, second]
    assert bus.stats.processed == 2
    assert bus.stats.received == 2
    assert bus.pending == 0
    await bus.stop()


async def test_queue_overflow_drops_and_counts(caplog: pytest.LogCaptureFixture) -> None:
    processor = Recording()
    processor.hold = True
    bus = EventBus(processor, max_pending=1, workers=1, event_timeout_s=0)
    await bus.start()
    assert bus.commit(event("held"))
    await processor.started.wait()
    assert bus.commit(event("queued"))
    bus._last_drop_log = 0.0  # pyright: ignore[reportPrivateUsage]
    with caplog.at_level(logging.WARNING, logger="verter"):
        assert not bus.commit(event("dropped"))
    assert bus.stats.dropped == 1
    assert "dropping events" in caplog.text
    processor.release.set()
    await bus.wait_idle()
    assert bus.stats.processed == 2
    await bus.stop()


async def test_events_are_dropped_when_the_bus_is_not_accepting() -> None:
    bus = EventBus(Recording(), workers=1)
    assert not bus.commit(event())
    assert bus.stats.dropped == 1
    await bus.start()
    await bus.stop()
    assert not bus.commit(event())
    assert bus.stats.dropped == 2


async def test_session_interceptor_consumes_before_the_queue() -> None:
    processor = Recording()
    bus = EventBus(processor, workers=1, event_timeout_s=0)
    await bus.start()
    captured: list[MessageEvent] = []
    target = event("next")

    def intercept(item: MessageEvent) -> bool:
        captured.append(item)
        return True

    bus.set_interceptor(target.unified_msg_origin, intercept)
    assert bus.commit(target)
    other = event("elsewhere", sender="other")
    assert bus.commit(other)
    await bus.wait_idle()
    assert captured == [target]
    assert processor.events == [other]
    assert bus.stats.intercepted == 1

    def refuse(item: MessageEvent) -> bool:
        return False

    bus.set_interceptor(target.unified_msg_origin, refuse)
    bus.remove_interceptor(target.unified_msg_origin, intercept)
    assert bus.commit(event("still-queued"))
    await bus.wait_idle()
    assert bus.stats.intercepted == 1
    bus.remove_interceptor(target.unified_msg_origin, refuse)
    await bus.stop()


async def test_handler_failures_do_not_stop_the_worker() -> None:
    processor = Recording()
    processor.error = RuntimeError("plugin exploded")
    bus = EventBus(processor, workers=1, event_timeout_s=0)
    await bus.start()
    assert bus.commit(event())
    await bus.wait_idle()
    assert bus.stats.failed == 1
    assert bus.stats.processed == 0
    processor.error = None
    assert bus.commit(event("recovered"))
    await bus.wait_idle()
    assert bus.stats.processed == 1
    await bus.stop()


async def test_event_timeout_cancels_a_stuck_handler() -> None:
    processor = Recording()
    processor.hold = True
    bus = EventBus(processor, workers=1, event_timeout_s=0.05)
    await bus.start()
    assert bus.commit(event())
    await processor.started.wait()
    await asyncio.wait_for(bus.wait_idle(), 1)
    assert bus.stats.timed_out == 1
    assert bus.in_flight == 0
    await bus.stop()


async def test_stop_cancels_work_that_does_not_drain() -> None:
    processor = Recording()
    processor.hold = True
    bus = EventBus(processor, max_pending=4, workers=1, event_timeout_s=0)
    await bus.start()
    assert bus.commit(event("stuck"))
    await processor.started.wait()
    assert bus.commit(event("queued"))
    await bus.stop(drain_timeout_s=0.05)
    assert not bus.commit(event("after"))
    assert bus.in_flight == 0
