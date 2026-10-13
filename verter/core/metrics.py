"""Pipeline latency histogram. Event counts live on the event bus."""

from __future__ import annotations

from dataclasses import dataclass

# Upper edges in microseconds. The last counter is everything above the final edge.
BUCKETS_US: tuple[int, ...] = (50, 100, 250, 500, 1_000, 2_500, 10_000, 50_000, 250_000)


@dataclass(frozen=True, slots=True)
class HistogramSnapshot:
    buckets_us: tuple[int, ...]
    counts: tuple[int, ...]
    total: int
    sum_us: float


class LatencyHistogram:
    """Fixed-bucket histogram of how long one event spent inside the pipeline."""

    __slots__ = ("_counts", "sum_us", "total")

    def __init__(self) -> None:
        self._counts = [0] * (len(BUCKETS_US) + 1)
        self.total = 0
        self.sum_us = 0.0

    def observe(self, seconds: float) -> None:
        microseconds = seconds * 1_000_000
        self.total += 1
        self.sum_us += microseconds
        for index, edge in enumerate(BUCKETS_US):
            if microseconds <= edge:
                self._counts[index] += 1
                return
        self._counts[-1] += 1

    def snapshot(self) -> HistogramSnapshot:
        return HistogramSnapshot(
            buckets_us=BUCKETS_US,
            counts=tuple(self._counts),
            total=self.total,
            sum_us=self.sum_us,
        )
