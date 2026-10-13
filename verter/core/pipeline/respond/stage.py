from __future__ import annotations

from verter.core.log import logger
from verter.core.pipeline.context import HookPoint, PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


@register_stage
class RespondStage(Stage):
    """Sends the current result and clears it, so each yielded result is sent once."""

    delivers = True

    async def initialize(self, ctx: PipelineContext) -> None:
        self._handlers = ctx.handlers

    async def process(self, event: MessageEvent) -> None:
        result = event.get_result()
        if result is None:
            return
        if not result.chain:
            event.clear_result()
            return
        try:
            await event.send(result)
        except Exception:  # noqa: BLE001 - adapter send errors must not fail the event
            logger.exception("Failed to send a reply to %s", event.unified_msg_origin)
            event.clear_result()
            return
        await self._handlers.run_hooks(HookPoint.AFTER_MESSAGE_SENT, event)
        event.clear_result()
