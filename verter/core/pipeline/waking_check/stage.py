"""Decides whether the bot was addressed and which handlers accept the event.

The bot wakes when the message starts with a wake prefix (unless it is a group
message that mentions someone else first), mentions or quotes the bot, is a
private chat (configurable), or when any handler matches.
"""

from __future__ import annotations

from collections.abc import Sequence

from verter.core.log import logger
from verter.core.message.components import At, AtAll, MessageComponent, Reply
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


def _addresses_bot(
    messages: Sequence[MessageComponent], self_id: str, *, ignore_at_all: bool
) -> bool:
    for component in messages:
        if isinstance(component, At) and component.target == self_id:
            return True
        if isinstance(component, AtAll) and not ignore_at_all:
            return True
        if isinstance(component, Reply) and component.sender_id == self_id:
            return True
    return False


@register_stage
class WakingCheckStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self._ctx = ctx
        self._settings = ctx.config.platform_settings
        self._prefixes = tuple(sorted(ctx.config.wake_prefix, key=len, reverse=True))
        self._admins = frozenset(ctx.config.admins_id)

    async def process(self, event: MessageEvent) -> None:
        settings = self._settings
        sender = event.get_sender_id()
        self_id = event.get_self_id()
        if settings.ignore_bot_self_message and sender == self_id:
            event.stop_event()
            return
        if sender in self._admins:
            event.role = "admin"
        text = event.message_str.strip()
        messages = event.get_messages()
        private = event.is_private_chat()
        for prefix in self._prefixes:
            if not text.startswith(prefix):
                continue
            first = messages[0] if messages else None
            if not private and isinstance(first, At) and first.target != self_id:
                break
            event.is_wake = event.is_at_or_wake_command = True
            text = text[len(prefix) :].strip()
            break
        event.message_str = text
        if not event.is_wake and (
            _addresses_bot(messages, self_id, ignore_at_all=settings.ignore_at_all)
            or (private and not settings.friend_message_needs_wake_prefix)
        ):
            event.is_wake = event.is_at_or_wake_command = True

        event.activated_handlers = await self._ctx.handlers.match(event)
        if event.activated_handlers:
            event.is_wake = True
        if not event.is_wake:
            event.stop_event()
            return
        logger.info(
            "[%s] %s/%s: %s",
            event.get_platform_id(),
            event.get_sender_name() or "-",
            sender,
            event.get_message_outline(),
        )
