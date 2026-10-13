from __future__ import annotations

from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


@register_stage
class PreProcessStage(Stage):
    """Extension point between filtering and processing; events pass through unchanged."""

    async def initialize(self, ctx: PipelineContext) -> None:
        return None

    async def process(self, event: MessageEvent) -> None:
        return None
