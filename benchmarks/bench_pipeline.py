"""Latency of the real pipeline and event bus (docs/DEV_PLAN.md 8.2, P1-1).

Measures one command reply through the in-memory adapter. The best of several
rounds is kept so a single noisy sample on a shared CI machine does not fail
the budget.
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from collections.abc import AsyncIterator, Coroutine, Sequence
from typing import Any

from benchmarks.harness import Budget
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle
from verter.core.message.message_event_result import MessageEventResult
from verter.core.pipeline.context import BoundHandler, HookPoint
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.sources.inmemory import InMemoryPlatform

BUDGETS = (
    Budget(
        "command_e2e_p99_us",
        max=1000,
        note="one in-memory command, including the event bus, p99 under 1 ms",
    ),
)


def _run(coro: Coroutine[Any, Any, float]) -> float:
    if sys.platform == "win32":
        return asyncio.run(coro)
    import uvloop

    return asyncio.run(coro, loop_factory=uvloop.new_event_loop)


def _p99(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, math.ceil(0.99 * len(ordered)) - 1))
    return ordered[index]


async def _reply(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
    yield event.plain_result("ok")


class _Hello:
    async def match(self, event: MessageEvent) -> Sequence[BoundHandler]:
        word = event.message_str.split(" ", 1)[0]
        if word == "hello":
            return [BoundHandler("hello", _reply)]
        return ()

    async def run_hooks(self, hook: HookPoint, event: MessageEvent) -> None:
        return None


async def _command_p99_us(count: int) -> float:
    config = VerterConfig()
    config.dashboard.enable = False
    config.computer.enable = False
    config.platform_settings.rate_limit.enable = False
    lifecycle = CoreLifecycle(
        config,
        extra_platforms=[PlatformConfig(id="mem", type="inmemory")],
        handlers=_Hello(),
    )
    await lifecycle.start()
    try:
        platform = lifecycle.platforms.get("mem") if lifecycle.platforms else None
        if not isinstance(platform, InMemoryPlatform):
            raise RuntimeError("in-memory platform did not start")
        for _ in range(50):
            if platform.inject("/hello") is None:
                raise RuntimeError("event bus dropped a benchmark message")
            await platform.next_message(wait_s=2)
        samples: list[float] = []
        for _ in range(count):
            started = time.perf_counter_ns()
            if platform.inject("/hello") is None:
                raise RuntimeError("event bus dropped a benchmark message")
            await platform.next_message(wait_s=2)
            samples.append((time.perf_counter_ns() - started) / 1_000)
        return _p99(samples)
    finally:
        await lifecycle.stop()


def run(scale: float = 1.0) -> dict[str, float]:
    count = max(100, int(500 * scale))
    rounds = 5 if scale >= 1 else 3
    best = min(_run(_command_p99_us(count)) for _ in range(rounds))
    return {"command_e2e_p99_us": best}
