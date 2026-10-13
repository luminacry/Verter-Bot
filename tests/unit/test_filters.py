"""Decorators must stack onto one draft and survive binding into the index."""

from collections.abc import AsyncIterator

import pytest

from tests.helpers import make_inmemory
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_type import MessageType
from verter.core.star import filter as filt
from verter.core.star.filter import (
    PermissionType,
    after_message_sent,
    command,
    custom_filter,
    event_message_type,
    handlers_in,
    on_decorating_result,
    permission_type,
    platform_adapter_type,
    regex,
)
from verter.core.star.handler_index import HandlerIndex, bind_handler


def _forget(*funcs: object) -> None:
    filt._DRAFTS[:] = [draft for draft in filt._DRAFTS if draft.func not in funcs]  # pyright: ignore[reportPrivateUsage]


def test_stacked_filters_bind_into_the_index() -> None:
    class Probe:
        @command("go")
        @permission_type(PermissionType.ADMIN)
        @platform_adapter_type("inmemory")
        @custom_filter(lambda event: event.message_str == "go")
        async def go(self, event: MessageEvent) -> str:
            return "ok"

        @regex("secret")
        @event_message_type(MessageType.GROUP_MESSAGE)
        async def secret(self, event: MessageEvent) -> AsyncIterator[str]:
            yield "found"

        @on_decorating_result()
        async def decorate(self, event: MessageEvent) -> None:
            return None

        @after_message_sent(priority=3)
        async def sent(self, event: MessageEvent) -> None:
            return None

    funcs = (Probe.go, Probe.secret, Probe.decorate, Probe.sent)
    try:
        drafts = [draft for draft in handlers_in(Probe.__module__) if draft.func in funcs]
        by_name = {draft.func.__name__: draft for draft in drafts}
        assert set(by_name) == {"go", "secret", "decorate", "sent"}
        assert by_name["go"].permission == "admin"
        assert by_name["go"].platforms == frozenset({"inmemory"})
        assert by_name["secret"].kind == "regex"
        assert by_name["secret"].message_types == frozenset({MessageType.GROUP_MESSAGE})
        assert by_name["sent"].priority == 3

        index = HandlerIndex.build([bind_handler("probe", Probe(), draft) for draft in drafts])
        platform = make_inmemory()
        member = platform.build_event("/go")
        member.is_at_or_wake_command = True
        member.message_str = "go"
        assert index.match(member) == []

        admin = platform.build_event("/go", sender_id="root")
        admin.is_at_or_wake_command = True
        admin.message_str = "go"
        admin.role = "admin"
        matched = index.match(admin)
        assert [handler.name for handler in matched] == ["probe.go"]

        group = platform.build_event("a secret", group_id="g")
        assert any(handler.name == "probe.secret" for handler in index.match(group))
        assert index.hooks
    finally:
        _forget(*funcs)


async def test_bound_handler_rejects_a_bad_yield() -> None:
    class Host:
        @command("bad")
        async def bad(self, event: MessageEvent) -> AsyncIterator[int]:
            yield 1

    try:
        draft = next(item for item in handlers_in(Host.__module__) if item.func is Host.bad)
        handler = bind_handler("probe", Host(), draft)
        event = make_inmemory().build_event("/bad")
        with pytest.raises(TypeError, match="expected a result or str"):
            async for _ in handler.run(event):
                pass
    finally:
        _forget(Host.bad)
