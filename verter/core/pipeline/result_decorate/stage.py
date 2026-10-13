"""Applies reply prefix, mention, quote, and the output keyword filter to a result."""

from __future__ import annotations

from verter.core.log import logger
from verter.core.message.components import At, Plain, Reply
from verter.core.pipeline.content_safety_check.stage import compile_keywords
from verter.core.pipeline.context import HookPoint, PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


@register_stage
class ResultDecorateStage(Stage):
    delivers = True

    async def initialize(self, ctx: PipelineContext) -> None:
        self._handlers = ctx.handlers
        self._settings = ctx.config.platform_settings
        safety = ctx.config.content_safety
        self._output_filter = compile_keywords(safety) if safety.also_use_in_response else None

    async def process(self, event: MessageEvent) -> None:
        result = event.get_result()
        if result is None or not result.chain:
            return
        await self._handlers.run_hooks(HookPoint.ON_DECORATING_RESULT, event)
        result = event.get_result()
        if result is None or not result.chain:
            return
        if self._output_filter is not None and self._output_filter.search(result.plain_text()):
            logger.info("Withheld a reply to %s by content safety", event.unified_msg_origin)
            event.clear_result()
            return

        settings = self._settings
        chain = result.chain
        if settings.reply_prefix:
            first = chain[0]
            if isinstance(first, Plain):
                chain[0] = Plain(settings.reply_prefix + first.text)
            else:
                chain.insert(0, Plain(settings.reply_prefix))
        if not event.is_private_chat():
            if settings.reply_with_mention:
                chain[0:0] = [At(event.get_sender_id(), event.get_sender_name()), Plain(" ")]
            if settings.reply_with_quote:
                chain.insert(0, Reply(event.message_obj.message_id, event.get_sender_id()))
