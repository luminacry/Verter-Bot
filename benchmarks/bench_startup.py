"""Cold start and idle memory of the core runtime (docs/P2_P4_PLAN.md).

Measured in a fresh interpreter so earlier benchmarks cannot inflate the RSS.
Idle RSS includes OneBot reverse WS and Telegram long polling against in-process
mocks, with no third-party plugins and the dashboard left off.
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
        max=80,
        note="OneBot + Telegram mocks, no third-party plugins, dashboard off",
    ),
)

_CHILD = r"""
import time

started = time.perf_counter()

import asyncio
import sys
import tempfile
from pathlib import Path

from tests.fakes.onebot import MockOneBotClient
from tests.fakes.telegram import MockTelegram
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle
from verter.core.platform.sources.onebot_v11 import OneBotV11Platform


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
    telegram = MockTelegram()
    await telegram.start()
    with tempfile.TemporaryDirectory(prefix="verter-bench-") as data:
        lifecycle = CoreLifecycle(
            config,
            data_dir=Path(data),
            extra_platforms=[
                PlatformConfig.model_validate(
                    {
                        "id": "qq",
                        "type": "onebot_v11",
                        "ws_reverse_host": "127.0.0.1",
                        "ws_reverse_port": 0,
                    }
                ),
                PlatformConfig.model_validate(
                    {
                        "id": "tg",
                        "type": "telegram",
                        "token": "123:abc",
                        "api_base": telegram.api_base,
                        "poll_timeout_s": 1,
                        "throttle_s": 0,
                    }
                ),
            ],
        )
        await lifecycle.start()
        assert lifecycle.platforms is not None
        onebot = lifecycle.platforms.get("qq")
        assert isinstance(onebot, OneBotV11Platform)
        deadline = time.perf_counter() + 2
        while onebot.bound_port is None:
            if time.perf_counter() > deadline:
                raise RuntimeError("OneBot reverse WS did not bind")
            await asyncio.sleep(0.01)
        peer = MockOneBotClient(f"ws://127.0.0.1:{onebot.bound_port}/")
        await peer.connect()
        elapsed_ms = (time.perf_counter() - started) * 1000
        rss = rss_mb()
        print(f"{elapsed_ms:.3f} {rss:.3f}")
        await peer.close()
        await lifecycle.stop()
        await telegram.close()


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
