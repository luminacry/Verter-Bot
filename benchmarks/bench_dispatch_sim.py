"""HandlerIndex cost for unwoken messages (docs/DEV_PLAN.md 8.2).

Replaces the P0 scan-versus-dict simulation. Command handlers are not visited
when the message was not woken, so the cost stays flat as the handler count grows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from benchmarks.harness import Budget, best_ns_per_op
from tests.helpers import make_inmemory
from verter.core.message.message_event_result import MessageEventResult
from verter.core.platform.message_event import MessageEvent
from verter.core.star.handler_index import HandlerIndex, RegisteredHandler

BUDGETS = (
    Budget(
        "indexed_growth_100_to_5000",
        max=1.10,
        note="unwoken dispatch must not grow with the command-handler count",
    ),
)


async def _noop(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
    if False:
        yield event.plain_result("")


def _index(count: int) -> HandlerIndex:
    handlers = [
        RegisteredHandler(
            name=f"p.cmd{i}",
            priority=0,
            kind="command",
            command=f"cmd{i}",
            parent=None,
            pattern=None,
            message_types=None,
            platforms=None,
            permission=None,
            hook=None,
            extras=(),
            run=_noop,
        )
        for i in range(count)
    ]
    return HandlerIndex.build(handlers)


def run(scale: float = 1.0) -> dict[str, float]:
    count = max(1_000, int(200_000 * scale))
    event = make_inmemory().build_event("just chatting", group_id="g")
    small = _index(100)
    large = _index(5_000)

    def measure(index: HandlerIndex) -> float:
        def work() -> int:
            match = index.match
            for _ in range(count):
                match(event)
            return count

        return best_ns_per_op(work, repeat=5)

    ratios: list[float] = []
    small_ns: list[float] = []
    large_ns: list[float] = []
    for _ in range(5):
        left = measure(small)
        right = measure(large)
        small_ns.append(left)
        large_ns.append(right)
        ratios.append(right / left)
    return {
        "indexed_ns_per_event_100": min(small_ns),
        "indexed_ns_per_event_5000": min(large_ns),
        "indexed_growth_100_to_5000": min(ratios),
    }
