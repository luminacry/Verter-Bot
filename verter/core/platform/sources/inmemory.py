"""In-process adapter for tests and benchmarks: inject messages, collect replies."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass

from verter.core.config import PlatformConfig
from verter.core.message.components import MessageComponent, Plain
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata

SELF_ID = "verter"


@dataclass(frozen=True, slots=True)
class SentMessage:
    session: str
    chain: MessageChain
    reply_to: MessageEvent | None
    sent_at: float


class InMemoryEvent(MessageEvent):
    __slots__ = ("_platform",)

    def __init__(
        self,
        message_str: str,
        message_obj: BotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        platform: InMemoryPlatform,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._platform = platform

    async def _send(self, message: MessageChain) -> None:
        self._platform.record(self.unified_msg_origin, message, self)


class InMemoryPlatform(Platform):
    def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
        super().__init__(config, context)
        self.sent: asyncio.Queue[SentMessage] = asyncio.Queue()
        self._closed = asyncio.Event()
        self._ids = itertools.count(1)
        self._meta = PlatformMetadata("inmemory", config.id, "In-process test adapter")

    def meta(self) -> PlatformMetadata:
        return self._meta

    async def run(self) -> None:
        await self._closed.wait()

    async def terminate(self) -> None:
        self._closed.set()

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        self.record(str(session), message, None)

    def record(self, session: str, chain: MessageChain, reply_to: MessageEvent | None) -> None:
        self.sent.put_nowait(SentMessage(session, chain, reply_to, time.perf_counter()))

    def build_event(
        self,
        text: str = "",
        *,
        sender_id: str = "user",
        sender_name: str = "",
        group_id: str | None = None,
        components: Sequence[MessageComponent] | None = None,
    ) -> InMemoryEvent:
        """Create an event as if ``sender_id`` sent ``text`` (or ``components``)."""
        message: list[MessageComponent] = (
            list(components) if components is not None else [Plain(text)]
        )
        message_str = "".join(c.text for c in message if isinstance(c, Plain))
        kind = MessageType.FRIEND_MESSAGE if group_id is None else MessageType.GROUP_MESSAGE
        session_id = sender_id if group_id is None else group_id
        bot_message = BotMessage(
            type=kind,
            self_id=SELF_ID,
            session_id=session_id,
            message_id=str(next(self._ids)),
            sender=MessageMember(sender_id, sender_name),
            message=message,
            message_str=message_str,
            group=Group(group_id) if group_id is not None else None,
        )
        return InMemoryEvent(message_str, bot_message, self._meta, session_id, self)

    def inject(
        self,
        text: str = "",
        *,
        sender_id: str = "user",
        sender_name: str = "",
        group_id: str | None = None,
        components: Sequence[MessageComponent] | None = None,
    ) -> InMemoryEvent | None:
        """Build an event and commit it; returns None if the event bus dropped it."""
        event = self.build_event(
            text,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            components=components,
        )
        return event if self.commit_event(event) else None

    async def next_message(self, wait_s: float = 1.0) -> SentMessage:
        return await asyncio.wait_for(self.sent.get(), wait_s)
