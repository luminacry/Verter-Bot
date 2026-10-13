"""Keyword filter over incoming text; ``ResultDecorateStage`` reuses it for replies."""

from __future__ import annotations

import re

from verter.core.config import ContentSafetyConfig
from verter.core.log import logger
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


def compile_keywords(config: ContentSafetyConfig) -> re.Pattern[str] | None:
    if not config.enable or not config.keywords:
        return None
    return re.compile("|".join(f"(?:{keyword})" for keyword in config.keywords))


@register_stage
class ContentSafetyCheckStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self._pattern = compile_keywords(ctx.config.content_safety)

    async def process(self, event: MessageEvent) -> None:
        if self._pattern is not None and self._pattern.search(event.message_str):
            logger.info("Blocked a message from %s by content safety", event.unified_msg_origin)
            event.stop_event()
