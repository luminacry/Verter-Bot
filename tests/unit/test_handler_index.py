from collections.abc import AsyncIterator

from tests.helpers import make_inmemory
from verter.core.message.message_event_result import MessageEventResult
from verter.core.pipeline.context import HookPoint
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_type import MessageType
from verter.core.star.handler_index import HandlerIndex, RegisteredHandler


def _handler(
    name: str,
    *,
    kind: str = "command",
    command: str | None = None,
    parent: str | None = None,
    priority: int = 0,
    permission: str | None = None,
    platforms: frozenset[str] | None = None,
    message_types: frozenset[MessageType] | None = None,
    pattern: str | None = None,
    hook: HookPoint | None = None,
    calls: list[str] | None = None,
) -> RegisteredHandler:
    import re

    async def run(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        if calls is not None:
            calls.append(name)
        yield event.plain_result(name)

    return RegisteredHandler(
        name=name,
        priority=priority,
        kind=kind,
        command=command,
        parent=parent,
        pattern=re.compile(pattern) if pattern is not None else None,
        message_types=message_types,
        platforms=platforms,
        permission=permission,
        hook=hook,
        extras=(),
        run=run,
    )


def test_unwoken_messages_do_not_touch_command_handlers() -> None:
    calls: list[str] = []
    index = HandlerIndex.build(
        [_handler(f"cmd{i}", command=f"cmd{i}", calls=calls) for i in range(50)]
    )
    event = make_inmemory().build_event("hello", group_id="g")
    assert index.match(event) == []
    assert calls == []
    assert index.passive is False


def test_commands_groups_regex_and_listeners() -> None:
    index = HandlerIndex.build(
        [
            _handler("low", command="go", priority=0),
            _handler("high", command="go", priority=10),
            _handler("sub", command="list", parent="tool"),
            _handler("heard", kind="listener"),
            _handler(
                "group-only",
                kind="listener",
                message_types=frozenset({MessageType.GROUP_MESSAGE}),
            ),
            _handler("secret", kind="regex", pattern="secret"),
            _handler("admins", command="go", permission="admin"),
            _handler("qq-only", command="go", platforms=frozenset({"qq"})),
        ]
    )
    platform = make_inmemory()
    woken = platform.build_event("/go")
    woken.is_at_or_wake_command = True
    woken.message_str = "go"
    names = [handler.name for handler in index.match(woken)]
    assert names[0] == "high"
    assert "low" in names
    assert "heard" in names
    assert "admins" not in names
    assert "qq-only" not in names
    woken.role = "admin"
    assert "admins" in [handler.name for handler in index.match(woken)]

    group = platform.build_event("tool list", group_id="g")
    group.is_at_or_wake_command = True
    group.message_str = "tool list"
    assert any(handler.name == "sub" for handler in index.match(group))
    assert any(handler.name == "group-only" for handler in index.match(group))

    private = platform.build_event("nothing secret here")
    assert [handler.name for handler in index.match(private)] == ["heard", "secret"]
    assert "tool list" in index.command_names
    assert "go" in index.command_names
