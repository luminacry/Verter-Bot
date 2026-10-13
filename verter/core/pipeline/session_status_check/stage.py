from __future__ import annotations

from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


@register_stage
class SessionStatusCheckStage(Stage):
    """Drops events from conversations an admin has switched off."""

    async def initialize(self, ctx: PipelineContext) -> None:
        self._sessions = ctx.sessions

    async def process(self, event: MessageEvent) -> None:
        if not self._sessions.is_enabled(event.unified_msg_origin):
            event.stop_event()
