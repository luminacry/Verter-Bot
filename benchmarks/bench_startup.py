"""Cold start and idle memory of the core runtime (docs/P1_S0_PLAN.md, P1-3).

Measured in a fresh interpreter so earlier benchmarks cannot inflate the RSS.
The 80 MB figure in the development plan includes OneBot and Telegram, which
land in P2. This budget is the core process with plugins loaded and the
dashboard left off, and it is temporary until those adapters exist.
"""

from __future__ import annotations

import subprocess
import sys

from benchmarks.harness import Budget

BUDGETS = (
    Budget(
        "cold_start_ms",
        max=2000,
        note="fresh process, import plus start, until a message can be accepted",
    ),
    Budget(
        "idle_rss_mb",
        max=120,
        note="temporary P1 ceiling for the core process; the 80 MB plan figure waits for P2",
    ),
)

_CHILD = r"""
import time

started = time.perf_counter()

import asyncio
import sys
import tempfile
from pathlib import Path

from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle


def rss_mb() -> float:
    if sys.platform.startswith("linux"):
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return rss / (1024 * 1024)
    return rss / 1024


async def main() -> None:
    config = VerterConfig()
    config.dashboard.enable = False
    config.computer.enable = False
    config.platform_settings.rate_limit.enable = False
    with tempfile.TemporaryDirectory(prefix="verter-bench-") as data:
        lifecycle = CoreLifecycle(
            config,
            data_dir=Path(data),
            extra_platforms=[PlatformConfig(id="mem", type="inmemory")],
        )
        await lifecycle.start()
        elapsed_ms = (time.perf_counter() - started) * 1000
        rss = rss_mb()
        print(f"{elapsed_ms:.3f} {rss:.3f}")
        await lifecycle.stop()


if sys.platform == "win32":
    asyncio.run(main())
else:
    import uvloop

    asyncio.run(main(), loop_factory=uvloop.new_event_loop)
"""


def _once() -> tuple[float, float]:
    completed = subprocess.run(  # noqa: S603 - this interpreter, constant script
        [sys.executable, "-c", _CHILD],
        check=True,
        capture_output=True,
        text=True,
    )
    elapsed, rss = completed.stdout.strip().split()
    return float(elapsed), float(rss)


def run(scale: float) -> dict[str, float]:
    rounds = 1 if scale < 1 else 3
    samples = [_once() for _ in range(rounds)]
    return {
        "cold_start_ms": min(sample[0] for sample in samples),
        "idle_rss_mb": max(sample[1] for sample in samples),
    }
