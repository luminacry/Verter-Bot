"""Handler decorators. Filters other than the dispatch key run only on candidates."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from verter.core.pipeline.context import HookPoint
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_type import MessageType

type ExtraFilter = Callable[[MessageEvent], bool]


class PermissionType(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(slots=True)
class Draft:
    """Mutable description built as decorators run, while the class body executes."""

    func: Callable[..., Any]
    kind: Literal["command", "regex", "listener", "hook"] = "listener"
    priority: int = 0
    command: str | None = None
    parent: str | None = None
    pattern: re.Pattern[str] | None = None
    message_types: frozenset[MessageType] | None = None
    platforms: frozenset[str] | None = None
    permission: str | None = None
    hook: HookPoint | None = None
    extras: list[ExtraFilter] = field(default_factory=list[ExtraFilter])


_DRAFTS: list[Draft] = []


def _draft(func: Callable[..., Any]) -> Draft:
    for draft in _DRAFTS:
        if draft.func is func:
            return draft
    draft = Draft(func)
    _DRAFTS.append(draft)
    return draft


def handlers_in(module: str) -> list[Draft]:
    return [draft for draft in _DRAFTS if draft.func.__module__ == module]


def drop_handlers(module: str) -> None:
    """Drop drafts for an unloaded module so the module dict can be collected."""
    _DRAFTS[:] = [draft for draft in _DRAFTS if draft.func.__module__ != module]


def command(name: str, *, priority: int = 0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Match a woken message whose first word is ``name`` (the wake prefix is already removed)."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        draft = _draft(func)
        draft.kind = "command"
        draft.command = name
        draft.parent = None
        draft.priority = priority
        return func

    return decorator


def command_group(name: str) -> _Group:
    """Group subcommands under ``/<name> <subcommand>``."""
    return _Group(name)


class _Group:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def command(
        self, name: str, *, priority: int = 0
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        parent = self.name

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            draft = _draft(func)
            draft.kind = "command"
            draft.command = name
            draft.parent = parent
            draft.priority = priority
            return func

        return decorator


def regex(pattern: str, *, priority: int = 0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Match message text. A match wakes the bot even without a wake prefix."""
    compiled = re.compile(pattern)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        draft = _draft(func)
        draft.kind = "regex"
        draft.pattern = compiled
        draft.priority = priority
        return func

    return decorator


def event_message_type(
    *kinds: MessageType,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Restrict a handler to these message types. Alone, it listens to every such message."""

    allowed = frozenset(kinds)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _draft(func).message_types = allowed
        return func

    return decorator


def platform_adapter_type(
    *names: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Restrict a handler to platform names or platform ids."""
    allowed = frozenset(names)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _draft(func).platforms = allowed
        return func

    return decorator


platform = platform_adapter_type


def permission_type(
    permission: PermissionType,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _draft(func).permission = permission.value
        return func

    return decorator


def custom_filter(
    predicate: ExtraFilter,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Extra predicate. It runs only after the handler is already a candidate."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _draft(func).extras.append(predicate)
        return func

    return decorator


def _hook(
    hook: HookPoint, *, priority: int = 0
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        draft = _draft(func)
        draft.kind = "hook"
        draft.hook = hook
        draft.priority = priority
        return func

    return decorator


def on_decorating_result(
    *, priority: int = 0
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return _hook(HookPoint.ON_DECORATING_RESULT, priority=priority)


def after_message_sent(*, priority: int = 0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return _hook(HookPoint.AFTER_MESSAGE_SENT, priority=priority)
