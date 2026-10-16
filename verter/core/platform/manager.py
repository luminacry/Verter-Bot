"""Starts configured adapters and keeps them running."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from verter.core.config import ConfigError, PlatformConfig
from verter.core.log import logger
from verter.core.platform.platform import EventSink, Platform, PlatformContext, PlatformStatus
from verter.core.platform.register import get_platform_adapter

_STABLE_RUN_S = 30.0
_TERMINATE_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class PlatformStat:
    id: str
    type: str
    status: PlatformStatus
    restarts: int
    last_error: str | None
    next_restart_at: float | None
    """Event-loop time the adapter will be retried, set only while backing off."""


class PlatformManager:
    def __init__(
        self,
        configs: Sequence[PlatformConfig],
        sink: EventSink,
        *,
        request_shutdown: Callable[[], None],
        restart_initial_s: float = 1.0,
        restart_max_s: float = 60.0,
    ) -> None:
        self._configs = [c for c in configs if c.enable]
        self._context = PlatformContext(sink=sink, request_shutdown=request_shutdown)
        self._restart_initial_s = restart_initial_s
        self._restart_max_s = restart_max_s
        self._platforms: dict[str, Platform] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._restarts: dict[str, int] = {}
        self._next_restart: dict[str, float] = {}
        self._restart_locks: dict[str, asyncio.Lock] = {}
        self._stopping = False

    @property
    def platforms(self) -> list[Platform]:
        return list(self._platforms.values())

    def get(self, platform_id: str) -> Platform | None:
        return self._platforms.get(platform_id)

    def stats(self) -> list[PlatformStat]:
        return [
            PlatformStat(
                id=pid,
                type=p.config.type,
                status=p.status,
                restarts=self._restarts[pid],
                last_error=p.last_error,
                next_restart_at=self._next_restart.get(pid),
            )
            for pid, p in self._platforms.items()
        ]

    async def start(self) -> None:
        """Create every enabled adapter and start supervising it.

        Raises:
            ConfigError: For unknown adapter types, duplicate ids, or invalid settings.
        """
        ids = [c.id for c in self._configs]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ConfigError(f"duplicate platform ids: {', '.join(duplicates)}")
        for config in self._configs:
            adapter = get_platform_adapter(config.type)
            self._platforms[config.id] = adapter(config, self._context)
            self._restarts[config.id] = 0
        for pid, platform in self._platforms.items():
            self._tasks[pid] = asyncio.create_task(
                self._supervise(platform), name=f"platform:{pid}"
            )
            logger.info("Platform %s (%s) started", pid, platform.config.type)

    async def restart(self, platform_id: str) -> None:
        """Stop one adapter and supervise a fresh run of it.

        Cancels the running supervisor first, so a backoff retry cannot race the
        manual restart. The backoff delay resets; the restart counter is kept.

        Raises:
            KeyError: If no adapter has that id.
            RuntimeError: If the manager is shutting down, or a restart is underway.
        """
        if self._stopping:
            raise RuntimeError("manager is stopping")
        platform = self._platforms.get(platform_id)
        if platform is None:
            raise KeyError(platform_id)
        lock = self._restart_locks.setdefault(platform_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("restart already in progress")
        async with lock:
            await self._restart_locked(platform)

    async def _restart_locked(self, platform: Platform) -> None:
        pid = platform.config.id
        task = self._tasks.pop(pid, None)
        if task is not None:
            # Let an in-flight crash settle into its backoff sleep before cancelling,
            # so the cancellation lands on the sleep rather than inside run().
            for _ in range(100):
                if platform.status is PlatformStatus.BACKOFF or task.done():
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        try:
            await asyncio.wait_for(platform.terminate(), _TERMINATE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - a stuck adapter must not block the restart
            logger.exception("Platform %s did not terminate cleanly", pid)
        self._next_restart.pop(pid, None)
        platform.status = PlatformStatus.STARTING
        self._tasks[pid] = asyncio.create_task(self._supervise(platform), name=f"platform:{pid}")
        logger.info("Platform %s restarted", pid)

    async def stop(self) -> None:
        self._stopping = True
        platforms, tasks = self._platforms, self._tasks
        self._platforms, self._tasks = {}, {}
        for platform in platforms.values():
            try:
                await asyncio.wait_for(platform.terminate(), _TERMINATE_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - one adapter must not block the others
                logger.exception("Platform %s did not terminate cleanly", platform.config.id)
        for task in tasks.values():
            task.cancel()
        for task in tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for platform in platforms.values():
            platform.status = PlatformStatus.STOPPED

    async def _supervise(self, platform: Platform) -> None:
        loop = asyncio.get_running_loop()
        delay = self._restart_initial_s
        pid = platform.config.id
        while True:
            platform.status = PlatformStatus.RUNNING
            started = loop.time()
            try:
                await platform.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - adapter crashes are restarted, not fatal
                platform.status = PlatformStatus.BACKOFF
                platform.last_error = f"{type(exc).__name__}: {exc}"
                if loop.time() - started >= _STABLE_RUN_S:
                    delay = self._restart_initial_s
                self._next_restart[pid] = loop.time() + delay
                logger.exception("Platform %s crashed; restarting in %.1fs", pid, delay)
                await asyncio.sleep(delay)
                self._next_restart.pop(pid, None)
                delay = min(delay * 2, self._restart_max_s)
                self._restarts[pid] += 1
                continue
            platform.status = PlatformStatus.STOPPED
            logger.info("Platform %s stopped", pid)
            return
