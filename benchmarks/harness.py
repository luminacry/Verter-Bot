"""Shared benchmark helpers: timing and budget checks."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Budget:
    """An inclusive bound on one metric reported by a benchmark module."""

    metric: str
    max: float | None = None
    min: float | None = None
    note: str = ""

    def describe(self) -> str:
        if self.max is not None and self.min is not None:
            return f"{self.min:g}..{self.max:g}"
        if self.max is not None:
            return f"<= {self.max:g}"
        if self.min is not None:
            return f">= {self.min:g}"
        return "-"

    def violation(self, value: float) -> str | None:
        if self.max is not None and value > self.max:
            return f"{self.metric} = {value:.3f} exceeds {self.max:g}"
        if self.min is not None and value < self.min:
            return f"{self.metric} = {value:.3f} is below {self.min:g}"
        return None


def evaluate(results: Mapping[str, float], budgets: Iterable[Budget]) -> list[str]:
    """Return one message per violated or missing budget."""
    problems: list[str] = []
    for budget in budgets:
        if budget.metric not in results:
            problems.append(f"{budget.metric} was not reported")
            continue
        message = budget.violation(results[budget.metric])
        if message:
            problems.append(message)
    return problems


def best_ns_per_op(work: Callable[[], int], *, repeat: int) -> float:
    """Run ``work`` ``repeat`` times and return the fastest nanoseconds per operation.

    ``work`` performs a batch and returns how many operations it executed. The
    minimum is used because noise only ever makes a run slower.
    """
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter_ns()
        ops = work()
        elapsed = time.perf_counter_ns() - start
        best = min(best, elapsed / max(ops, 1))
    return best
