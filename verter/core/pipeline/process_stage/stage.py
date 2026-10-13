"""Runs the handlers activated in ``WakingCheckStage``, highest priority first."""

from __future__ import annotations

from collections.abc import AsyncIterator

from verter.core.log import logger
from verter.core.message.message_event_result import MessageEventResult
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent

FAILURE_REPLY = "插件 {name} 处理消息时出错，详情请查看日志。"


@register_stage
class ProcessStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        return None

    async def process(self, event: MessageEvent) -> AsyncIterator[None]:
        for handler in event.activated_handlers:
            if event.is_stopped():
                return
            try:
                async for result in handler.run(event):
                    if result is not None:
                        event.set_result(result)
                    yield
                    if event.is_stopped():
                        return
            except Exception:  # noqa: BLE001 - plugin failures are contained per handler
                # Exception text can leak paths or secrets, so chat users get a generic notice.
                logger.exception("Handler %s failed on %s", handler.name, event.unified_msg_origin)
                if event.is_at_or_wake_command:
                    event.set_result(
                        MessageEventResult().message(FAILURE_REPLY.format(name=handler.name))
                    )
                    yield
                event.stop_event()
                return
            event.clear_result()
