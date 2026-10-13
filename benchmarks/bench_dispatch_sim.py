"""Cost-structure simulation of handler dispatch (docs/DEV_PLAN.md, appendix A).

Compares an indexed lookup with a per-message scan over every registered handler.
This is a P0 placeholder that exercises the budget gate; P1 replaces it with a
benchmark of the real HandlerIndex.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

from benchmarks.harness import Budget, best_ns_per_op

BUDGETS = (
    Budget(
        "indexed_growth_100_to_5000",
        max=1.25,
        note="indexed dispatch cost must not grow with the handler count",
    ),
)

_WS = re.compile(r"\s+")
_CHATTER = "今天 天气 不错  大家 吃了吗"


class _Event:
    __slots__ = ("is_wake", "text")

    def __init__(self, raw: str) -> None:
        self.is_wake = raw.startswith("/")
        self.text = raw[1:] if self.is_wake else raw


class _CommandFilter:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def matches(self, event: _Event) -> bool:
        if not event.is_wake:
            return False
        text = _WS.sub(" ", event.text.strip())
        return text == self.name or text.startswith(self.name + " ")


def _events(count: int, handlers: int, wake_ratio: float = 0.1) -> list[_Event]:
    rnd = random.Random(42)
    return [
        _Event(f"/cmd{rnd.randrange(handlers)} a b" if rnd.random() < wake_ratio else _CHATTER)
        for _ in range(count)
    ]


def _indexed(handlers: int) -> Callable[[_Event], object]:
    table = {f"cmd{i}": i for i in range(handlers)}

    def match(event: _Event) -> object:
        if not event.is_wake:
            return None
        return table.get(event.text.lstrip().partition(" ")[0])

    return match


def _scan(handlers: int) -> Callable[[_Event], object]:
    filters = [_CommandFilter(f"cmd{i}") for i in range(handlers)]

    def match(event: _Event) -> object:
        return [f for f in filters if f.matches(event)]

    return match


def _measure(match: Callable[[_Event], object], events: list[_Event], repeat: int) -> float:
    def work() -> int:
        for event in events:
            match(event)
        return len(events)

    return best_ns_per_op(work, repeat=repeat)


def run(scale: float = 1.0) -> dict[str, float]:
    count = max(1000, int(200_000 * scale))
    indexed_100 = _measure(_indexed(100), _events(count, 100), repeat=7)
    indexed_5000 = _measure(_indexed(5000), _events(count, 5000), repeat=7)
    scan_count = max(200, int(4_000 * scale))
    scan_1000 = _measure(_scan(1000), _events(scan_count, 1000), repeat=3)
    return {
        "indexed_ns_per_event_100": indexed_100,
        "indexed_ns_per_event_5000": indexed_5000,
        "indexed_growth_100_to_5000": indexed_5000 / indexed_100,
        "scan_ns_per_event_1000": scan_1000,
        "scan_over_indexed_1000": scan_1000 / indexed_100,
    }
