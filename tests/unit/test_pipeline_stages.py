import asyncio
from collections.abc import AsyncIterator

import pytest

from tests.helpers import ScriptedHandlers, make_harness, reply
from verter.core.config import VerterConfig
from verter.core.message.components import At, AtAll, Image, Plain, Reply
from verter.core.message.message_event_result import MessageEventResult
from verter.core.pipeline.context import HookPoint
from verter.core.pipeline.process_stage.stage import FAILURE_REPLY
from verter.core.pipeline.rate_limit_check.stage import RateLimitStage
from verter.core.platform.message_event import MessageEvent


def config(**platform_settings: object) -> VerterConfig:
    cfg = VerterConfig.model_validate({"platform_settings": platform_settings})
    cfg.platform_settings.rate_limit.enable = False
    return cfg


def hello() -> ScriptedHandlers:
    return ScriptedHandlers(commands={"hello": reply("hi")})


# Waking -----------------------------------------------------------------------


async def test_private_chat_wakes_without_prefix() -> None:
    h = await make_harness(handlers=hello())
    assert await h.send("hello") == ["hi"]
    assert await h.send("/hello") == ["hi"]


async def test_friend_prefix_requirement() -> None:
    h = await make_harness(config(friend_message_needs_wake_prefix=True), hello())
    assert await h.send("hello") == []
    assert await h.send("/hello") == ["hi"]


async def test_group_messages_need_a_prefix_or_mention() -> None:
    h = await make_harness(handlers=hello())
    assert await h.send("hello", group_id="g") == []
    assert await h.send("/ hello", group_id="g") == ["hi"]
    mention = h.platform.build_event(components=[At("verter"), Plain("hello")], group_id="g")
    assert [m.chain.outline() for m in await h.run(mention)] == ["hi"]


async def test_prefix_after_mentioning_someone_else_does_not_wake() -> None:
    h = await make_harness(handlers=hello())
    event = h.platform.build_event(components=[At("someone"), Plain("/hello")], group_id="g")
    assert await h.run(event) == []
    assert event.is_stopped()


@pytest.mark.parametrize(("ignore_at_all", "expected"), [(False, ["hi"]), (True, [])])
async def test_at_all(ignore_at_all: bool, expected: list[str]) -> None:
    h = await make_harness(config(ignore_at_all=ignore_at_all), hello())
    event = h.platform.build_event(components=[AtAll(), Plain("hello")], group_id="g")
    assert [m.chain.outline() for m in await h.run(event)] == expected


async def test_quoting_the_bot_wakes_it() -> None:
    h = await make_harness(handlers=hello())
    event = h.platform.build_event(
        components=[Reply("m1", sender_id="verter"), Plain("hello")], group_id="g"
    )
    assert [m.chain.outline() for m in await h.run(event)] == ["hi"]


async def test_longest_wake_prefix_wins() -> None:
    cfg = config()
    cfg.wake_prefix = ["/", "//"]
    h = await make_harness(cfg, hello())
    event = h.platform.build_event("//hello", group_id="g")
    assert [m.chain.outline() for m in await h.run(event)] == ["hi"]
    assert event.message_str == "hello"


async def test_messages_from_the_bot_itself_are_ignored() -> None:
    h = await make_harness(handlers=hello())
    assert await h.send("/hello", sender_id="verter") == []


async def test_admins_get_the_admin_role() -> None:
    cfg = config()
    cfg.admins_id = ["42"]
    h = await make_harness(cfg)
    admin = h.platform.build_event("/x", sender_id="42")
    member = h.platform.build_event("/x", sender_id="7")
    await h.run(admin)
    await h.run(member)
    assert admin.is_admin()
    assert not member.is_admin()


async def test_matching_listener_wakes_the_bot() -> None:
    h = await make_harness(handlers=ScriptedHandlers(listeners=[reply("heard")]))
    assert await h.send("just chatting", group_id="g") == ["heard"]


async def test_unmatched_wake_stops_without_reply() -> None:
    h = await make_harness()
    event = h.platform.build_event("/unknown")
    assert await h.run(event) == []
    assert event.is_wake


# Whitelist, session status, rate limit, content safety -------------------------


async def test_whitelist_blocks_unlisted_sessions_and_admin_bypass() -> None:
    cfg = config(enable_id_white_list=True, id_whitelist=["mem:GroupMessage:allowed", "solo"])
    cfg.admins_id = ["boss"]
    h = await make_harness(cfg, hello())
    assert await h.send("/hello", group_id="allowed") == ["hi"]
    assert await h.send("/hello", sender_id="solo") == ["hi"]
    assert await h.send("/hello", group_id="other") == []
    assert await h.send("/hello", group_id="other", sender_id="boss") == ["hi"]

    cfg.platform_settings.wl_ignore_admin_on_group = False
    strict = await make_harness(cfg, hello())
    assert await strict.send("/hello", group_id="other", sender_id="boss") == []


async def test_disabled_sessions_are_ignored() -> None:
    h = await make_harness(handlers=hello())
    h.ctx.sessions.disable("mem:FriendMessage:user")
    assert await h.send("/hello") == []
    h.ctx.sessions.enable("mem:FriendMessage:user")
    assert await h.send("/hello") == ["hi"]


async def test_rate_limit_discards_over_the_window() -> None:
    cfg = VerterConfig.model_validate({"platform_settings": {"rate_limit": {"count": 2}}})
    h = await make_harness(cfg, hello())
    assert [await h.send("/hello") for _ in range(3)] == [["hi"], ["hi"], []]
    assert await h.send("/hello", sender_id="other") == ["hi"]


async def test_rate_limit_stall_waits_for_the_window() -> None:
    cfg = VerterConfig.model_validate(
        {"platform_settings": {"rate_limit": {"count": 1, "window_s": 0.05, "strategy": "stall"}}}
    )
    h = await make_harness(cfg, hello())
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await h.send("/hello") == ["hi"]
    assert await h.send("/hello") == ["hi"]
    assert loop.time() - started >= 0.04


async def test_rate_limit_sweeps_idle_sessions() -> None:
    cfg = VerterConfig.model_validate({"platform_settings": {"rate_limit": {"window_s": 10}}})
    h = await make_harness(cfg, hello())
    stage = next(s for s in h.scheduler.stages if isinstance(s, RateLimitStage))
    now = [100.0]
    stage.clock = lambda: now[0]
    await h.send("/hello", sender_id="a")
    now[0] = 200.0
    await h.send("/hello", sender_id="b")
    stage._sweep(now[0])  # pyright: ignore[reportPrivateUsage]
    assert set(stage._hits) == {"mem:FriendMessage:b"}  # pyright: ignore[reportPrivateUsage]


async def test_content_safety_filters_input_and_output() -> None:
    cfg = config()
    cfg.content_safety.enable = True
    cfg.content_safety.keywords = ["forbidden", r"bad\w+"]
    handlers = ScriptedHandlers(commands={"hello": reply("hi"), "echo": reply("badword")})
    h = await make_harness(cfg, handlers)
    assert await h.send("/hello forbidden") == []
    assert await h.send("/echo") == []
    assert await h.send("/hello") == ["hi"]

    cfg.content_safety.also_use_in_response = False
    lenient = await make_harness(cfg, handlers)
    assert await lenient.send("/echo") == ["badword"]


# Processing -------------------------------------------------------------------


async def test_each_yield_is_delivered_in_order() -> None:
    async def chatty(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        yield event.plain_result("one")
        yield None
        yield event.plain_result("two")

    h = await make_harness(handlers=ScriptedHandlers(commands={"talk": [chatty, reply("three")]}))
    assert await h.send("/talk") == ["one", "two", "three"]


async def test_stop_event_skips_later_handlers_but_sends_the_yielded_result() -> None:
    async def final(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        yield event.plain_result("last").stop_event()
        yield event.plain_result("never")

    async def quiet_stop(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        event.stop_event()
        yield None

    h = await make_harness(
        handlers=ScriptedHandlers(
            commands={"a": [final, reply("skipped")], "b": [quiet_stop, reply("skipped")]}
        )
    )
    assert await h.send("/a") == ["last"]
    assert await h.send("/b") == []


async def test_handler_failure_sends_a_generic_notice_and_stops() -> None:
    async def broken(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        yield event.plain_result("partial")
        raise RuntimeError("secret path /etc/shadow")

    h = await make_harness(handlers=ScriptedHandlers(commands={"x": [broken, reply("skipped")]}))
    sent = await h.send("/x")
    assert sent == ["partial", FAILURE_REPLY.format(name="cmd.x.0")]
    assert "shadow" not in "".join(sent)


async def test_listener_failure_is_silent() -> None:
    async def broken(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        raise RuntimeError("boom")
        yield None

    h = await make_harness(handlers=ScriptedHandlers(listeners=[broken]))
    assert await h.send("chatter", group_id="g") == []


# Decoration and delivery ------------------------------------------------------


async def test_reply_prefix_mention_and_quote() -> None:
    cfg = config(reply_prefix="[bot] ", reply_with_mention=True, reply_with_quote=True)

    async def image(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        yield event.chain_result([Image.from_url("https://x/a.png")])

    h = await make_harness(cfg, ScriptedHandlers(commands={"hello": reply("hi"), "pic": image}))
    group = h.platform.build_event("/hello", sender_id="42", sender_name="Bob", group_id="g")
    (sent,) = await h.run(group)
    assert sent.chain.outline() == "[reply]@Bob [bot] hi"
    reply_part = sent.chain.chain[0]
    assert isinstance(reply_part, Reply)
    assert reply_part.message_id == group.message_obj.message_id
    assert await h.send("/hello") == ["[bot] hi"]
    assert await h.send("/pic") == ["[bot] [image]"]


async def test_hooks_run_and_can_withhold_a_reply() -> None:
    handlers = hello()
    h = await make_harness(handlers=handlers)
    assert await h.send("/hello") == ["hi"]
    assert handlers.hook_calls == [
        (HookPoint.ON_DECORATING_RESULT, "hello"),
        (HookPoint.AFTER_MESSAGE_SENT, "hello"),
    ]
    handlers.hook_actions[HookPoint.ON_DECORATING_RESULT] = lambda event: event.clear_result()
    assert await h.send("/hello") == []


async def test_empty_results_are_not_sent() -> None:
    async def empty(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        yield event.make_result()

    h = await make_harness(handlers=ScriptedHandlers(commands={"e": empty}))
    assert await h.send("/e") == []


async def test_send_failures_are_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    h = await make_harness(handlers=hello())

    def fail(*args: object) -> None:
        raise ConnectionError("platform down")

    monkeypatch.setattr(h.platform, "record", fail)
    event = h.platform.build_event("/hello")
    assert await h.run(event) == []
    assert event.get_result() is None
