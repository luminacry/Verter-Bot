"""Shared state handed to every pipeline stage, and the handler interface stages rely on."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from verter.core.config import VerterConfig
from verter.core.message.message_event_result import MessageEventResult
from verter.core.platform.message_event import MessageEvent


class HookPoint(StrEnum):
    ON_DECORATING_RESULT = "on_decorating_result"
    AFTER_MESSAGE_SENT = "after_message_sent"


@dataclass(frozen=True, slots=True)
class BoundHandler:
    """A matched handler with its arguments already parsed, ready to run.

    ``run`` yields results; each yielded result is decorated and sent before
    the handler resumes.
    """

    name: str
    run: Callable[[MessageEvent], AsyncIterator[MessageEventResult | None]]


class HandlerSource(Protocol):
    async def match(self, event: MessageEvent) -> Sequence[BoundHandler]:
        """Return the handlers that accept ``event``, highest priority first."""
        ...

    async def run_hooks(self, hook: HookPoint, event: MessageEvent) -> None:
        """Run the hooks registered for ``hook``; a hook may stop the event."""
        ...


class NullHandlerSource:
    async def match(self, event: MessageEvent) -> Sequence[BoundHandler]:
        return ()

    async def run_hooks(self, hook: HookPoint, event: MessageEvent) -> None:
        return None


class SessionStatusStore:
    """Conversations, by unified message origin, that the bot currently ignores."""

    def __init__(self) -> None:
        self._disabled: set[str] = set()

    def is_enabled(self, origin: str) -> bool:
        return origin not in self._disabled

    def disable(self, origin: str) -> None:
        self._disabled.add(origin)

    def enable(self, origin: str) -> None:
        self._disabled.discard(origin)


@dataclass(slots=True)
class PipelineContext:
    config: VerterConfig
    handlers: HandlerSource = field(default_factory=NullHandlerSource)
    sessions: SessionStatusStore = field(default_factory=SessionStatusStore)
