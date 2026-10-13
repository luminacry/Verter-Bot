"""Runs an event through the stages in ``STAGES_ORDER``."""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import AsyncGenerator, Awaitable
from typing import cast

from verter.core.pipeline.bootstrap import ensure_builtin_stages_registered
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, registered_stages
from verter.core.pipeline.stage_order import STAGES_ORDER
from verter.core.platform.message_event import MessageEvent


class PipelineScheduler:
    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self._stages: list[tuple[Stage, bool]] = []

    @property
    def stages(self) -> list[Stage]:
        return [stage for stage, _ in self._stages]

    async def initialize(self) -> None:
        """Instantiate and initialize every stage in ``STAGES_ORDER``.

        Raises:
            RuntimeError: If a stage named in ``STAGES_ORDER`` is not registered.
        """
        ensure_builtin_stages_registered()
        missing = [name for name in STAGES_ORDER if name not in registered_stages]
        if missing:
            raise RuntimeError(f"pipeline stages not registered: {', '.join(missing)}")
        stages: list[tuple[Stage, bool]] = []
        for name in STAGES_ORDER:
            stage = registered_stages[name]()
            await stage.initialize(self.ctx)
            stages.append((stage, inspect.isasyncgenfunction(stage.process)))
        self._stages = stages

    async def execute(self, event: MessageEvent) -> None:
        await self._run(event, 0)

    async def _run(self, event: MessageEvent, start: int) -> None:
        stages = self._stages
        for index in range(start, len(stages)):
            stage, is_generator = stages[index]
            if event.is_stopped() and not stage.delivers:
                return
            if not is_generator:
                await cast(Awaitable[None], stage.process(event))
                continue
            yielded = False
            generator = cast(AsyncGenerator[None], stage.process(event))
            async with contextlib.aclosing(generator):
                async for _ in generator:
                    yielded = True
                    await self._run(event, index + 1)
                    if event.is_stopped():
                        break
            if yielded:
                return
