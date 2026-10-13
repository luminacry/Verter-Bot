"""Immutable handler index. Unwoken messages do not scan command handlers."""

from __future__ import annotations

import inspect
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import cast

from verter.core.message.message_event_result import MessageEventResult
from verter.core.pipeline.context import BoundHandler, HookPoint
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_type import MessageType
from verter.core.star.filter import Draft

type HandlerRun = Callable[[MessageEvent], AsyncIterator[MessageEventResult | None]]


@dataclass(frozen=True, slots=True)
class RegisteredHandler:
    name: str
    priority: int
    kind: str
    command: str | None
    parent: str | None
    pattern: re.Pattern[str] | None
    message_types: frozenset[MessageType] | None
    platforms: frozenset[str] | None
    permission: str | None
    hook: HookPoint | None
    extras: tuple[Callable[[MessageEvent], bool], ...]
    run: HandlerRun

    def accepts(self, event: MessageEvent) -> bool:
        if self.message_types is not None and event.get_message_type() not in self.message_types:
            return False
        if self.platforms is not None and not (
            event.get_platform_name() in self.platforms or event.get_platform_id() in self.platforms
        ):
            return False
        if self.permission == "admin" and not event.is_admin():
            return False
        return all(extra(event) for extra in self.extras)


def bind_handler(plugin: str, instance: object, draft: Draft) -> RegisteredHandler:
    """Bind ``draft`` to ``instance`` and normalize its return into yielded results."""
    method: Callable[..., object] = getattr(instance, draft.func.__name__)
    name = f"{plugin}.{draft.func.__name__}"

    async def run(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        outcome = method(event)
        if inspect.isasyncgen(outcome):
            async for item in cast(AsyncIterator[object], outcome):
                coerced = _coerce(event, item)
                if coerced is not None:
                    yield coerced
            return
        if not inspect.isawaitable(outcome):
            raise TypeError(f"handler {name} must be async")
        coerced = _coerce(event, await cast(Awaitable[object], outcome))
        if coerced is not None:
            yield coerced

    return RegisteredHandler(
        name=name,
        priority=draft.priority,
        kind=draft.kind,
        command=draft.command,
        parent=draft.parent,
        pattern=draft.pattern,
        message_types=draft.message_types,
        platforms=draft.platforms,
        permission=draft.permission,
        hook=draft.hook,
        extras=tuple(draft.extras),
        run=run,
    )


def _coerce(event: MessageEvent, item: object) -> MessageEventResult | None:
    if item is None:
        return None
    if isinstance(item, MessageEventResult):
        return item
    if isinstance(item, str):
        return event.plain_result(item)
    raise TypeError(f"handler yielded {type(item).__name__}, expected a result or str")


@dataclass(frozen=True, slots=True)
class HandlerIndex:
    commands: dict[str, tuple[RegisteredHandler, ...]]
    groups: dict[str, dict[str, tuple[RegisteredHandler, ...]]]
    listeners: dict[MessageType | None, tuple[RegisteredHandler, ...]]
    regex: tuple[RegisteredHandler, ...]
    hooks: dict[HookPoint, tuple[RegisteredHandler, ...]]
    passive: bool
    command_names: tuple[str, ...]

    @classmethod
    def build(cls, handlers: Sequence[RegisteredHandler]) -> HandlerIndex:
        commands: dict[str, list[RegisteredHandler]] = defaultdict(list)
        groups: dict[str, dict[str, list[RegisteredHandler]]] = defaultdict(
            lambda: defaultdict(list)
        )
        listeners: dict[MessageType | None, list[RegisteredHandler]] = defaultdict(list)
        regex: list[RegisteredHandler] = []
        hooks: dict[HookPoint, list[RegisteredHandler]] = defaultdict(list)
        names: set[str] = set()
        for handler in handlers:
            if handler.kind == "hook":
                if handler.hook is not None:
                    hooks[handler.hook].append(handler)
                continue
            if handler.kind == "regex":
                regex.append(handler)
                continue
            if handler.kind == "command" and handler.command is not None:
                if handler.parent is None:
                    commands[handler.command].append(handler)
                    names.add(handler.command)
                else:
                    groups[handler.parent][handler.command].append(handler)
                    names.add(f"{handler.parent} {handler.command}")
                continue
            buckets = handler.message_types or [None]
            for bucket in buckets:
                listeners[bucket].append(handler)
        return cls(
            commands={key: _by_priority(value) for key, value in commands.items()},
            groups={
                key: {sub: _by_priority(value) for sub, value in subs.items()}
                for key, subs in groups.items()
            },
            listeners={key: _by_priority(value) for key, value in listeners.items()},
            regex=tuple(sorted(regex, key=lambda handler: handler.priority, reverse=True)),
            hooks={key: _by_priority(value) for key, value in hooks.items()},
            passive=bool(listeners or regex),
            command_names=tuple(sorted(names)),
        )

    def match(self, event: MessageEvent) -> list[BoundHandler]:
        """Return handlers that accept ``event``, highest priority first.

        When nothing is listening passively, a message that was not woken returns
        immediately. Command handlers are not scanned.
        """
        if not event.is_at_or_wake_command and not self.passive:
            return []
        found = self._passive(event)
        if event.is_at_or_wake_command:
            found.extend(self._commands(event))
        if len(found) > 1:
            found.sort(key=lambda handler: handler.priority, reverse=True)
        return [BoundHandler(handler.name, handler.run) for handler in found]

    def _commands(self, event: MessageEvent) -> list[RegisteredHandler]:
        head, _, tail = event.message_str.partition(" ")
        found = [handler for handler in self.commands.get(head, ()) if handler.accepts(event)]
        group = self.groups.get(head)
        if group is not None:
            sub = tail.partition(" ")[0]
            found.extend(handler for handler in group.get(sub, ()) if handler.accepts(event))
        return found

    def _passive(self, event: MessageEvent) -> list[RegisteredHandler]:
        if not self.passive:
            return []
        found = [
            handler
            for handler in self.listeners.get(event.get_message_type(), ())
            if handler.accepts(event)
        ]
        found.extend(handler for handler in self.listeners.get(None, ()) if handler.accepts(event))
        if self.regex:
            text = event.message_str
            found.extend(
                handler
                for handler in self.regex
                if handler.pattern is not None
                and handler.pattern.search(text)
                and handler.accepts(event)
            )
        return found


def _by_priority(handlers: list[RegisteredHandler]) -> tuple[RegisteredHandler, ...]:
    return tuple(sorted(handlers, key=lambda handler: handler.priority, reverse=True))
