"""Terminal adapter for local development: each stdin line is a message from one user."""

from __future__ import annotations

import asyncio
import itertools
import sys
import threading
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel, ConfigDict

from verter.core.config import PlatformConfig
from verter.core.message.components import At, MessageComponent, Plain
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata

SELF_ID = "verter"
_MENTION = f"@{SELF_ID}"


class ConsoleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_id: str = "console"
    sender_name: str = "Console"
    group_id: str = ""
    """When set, lines are sent as group messages in this group."""
    exit_on_eof: bool = True


class ConsoleEvent(MessageEvent):
    __slots__ = ("_platform",)

    def __init__(
        self,
        message_str: str,
        message_obj: BotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        platform: ConsolePlatform,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._platform = platform

    async def _send(self, message: MessageChain) -> None:
        self._platform.print_reply(message)


async def stdin_lines() -> AsyncIterator[str]:
    """Read stdin on a daemon thread so a pending read never blocks shutdown."""
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def push(item: str | None) -> None:
        try:
            loop.call_soon_threadsafe(lines.put_nowait, item)
        except RuntimeError:
            return

    def reader() -> None:
        try:
            for line in sys.stdin:
                push(line)
        finally:
            push(None)

    threading.Thread(target=reader, name="verter-console-stdin", daemon=True).start()
    while (line := await lines.get()) is not None:
        yield line


def _stdout_write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


class ConsolePlatform(Platform):
    """Lines starting with ``@verter`` mention the bot, which matters in group mode."""

    def __init__(
        self,
        config: PlatformConfig,
        context: PlatformContext,
        *,
        lines: Callable[[], AsyncIterator[str]] | None = None,
        write: Callable[[str], None] = _stdout_write,
    ) -> None:
        super().__init__(config, context)
        self.settings = self.parse_settings(ConsoleSettings)
        self._lines = lines
        self._write = write
        self._ids = itertools.count(1)
        self._meta = PlatformMetadata("console", config.id, "Terminal adapter for development")

    def meta(self) -> PlatformMetadata:
        return self._meta

    async def run(self) -> None:
        mode = f"group {self.settings.group_id}" if self.settings.group_id else "private chat"
        self._write(f"[console] chatting as {self.settings.sender_id} ({mode}); Ctrl+D to quit\n")
        async for raw in (self._lines or stdin_lines)():
            text = raw.rstrip("\r\n")
            if text.strip():
                self.commit_event(self.build_event(text))
        if self.settings.exit_on_eof:
            self.context.request_shutdown()

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        self.print_reply(message)

    def print_reply(self, message: MessageChain) -> None:
        self._write(f"{SELF_ID}> {message.outline()}\n")

    def build_event(self, text: str) -> ConsoleEvent:
        components: list[MessageComponent] = []
        if text == _MENTION or text.startswith(_MENTION + " "):
            components.append(At(SELF_ID))
            text = text[len(_MENTION) :].lstrip()
        if text:
            components.append(Plain(text))
        settings = self.settings
        group = Group(settings.group_id) if settings.group_id else None
        kind = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        session_id = settings.group_id or settings.sender_id
        message = BotMessage(
            type=kind,
            self_id=SELF_ID,
            session_id=session_id,
            message_id=str(next(self._ids)),
            sender=MessageMember(settings.sender_id, settings.sender_name),
            message=components,
            message_str=text,
            group=group,
        )
        return ConsoleEvent(text, message, self._meta, session_id, self)
