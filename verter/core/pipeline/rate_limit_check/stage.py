"""Sliding-window limit on woken events per conversation."""

from __future__ import annotations

import asyncio
import time
from collections import deque

from verter.core.log import logger
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent

_SWEEP_EVERY = 4096


@register_stage
class RateLimitStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        limit = ctx.config.platform_settings.rate_limit
        self._enabled = limit.enable
        self._count = limit.count
        self._window = limit.window_s
        self._stall = limit.strategy == "stall"
        self._hits: dict[str, deque[float]] = {}
        self._checks = 0
        self.clock = time.monotonic

    async def process(self, event: MessageEvent) -> None:
        if not self._enabled:
            return
        origin = event.unified_msg_origin
        hits = self._hits.get(origin)
        if hits is None:
            hits = self._hits[origin] = deque()
        now = self._expire(hits)
        if len(hits) >= self._count:
            wait = self._window - (now - hits[0])
            if not self._stall:
                logger.info("Rate limit reached for %s; dropping event", origin)
                event.stop_event()
                return
            logger.info("Rate limit reached for %s; waiting %.1fs", origin, wait)
            await asyncio.sleep(wait)
            now = self._expire(hits)
        hits.append(now)
        self._checks += 1
        if self._checks % _SWEEP_EVERY == 0:
            self._sweep(now)

    def _expire(self, hits: deque[float]) -> float:
        now = self.clock()
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        return now

    def _sweep(self, now: float) -> None:
        idle = [
            key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self._window
        ]
        for key in idle:
            del self._hits[key]
