import pytest

from tests.helpers import make_inmemory
from verter.core.message.components import At, Image, Plain
from verter.core.message.message_event_result import MessageChain, MessageEventResult
from verter.core.platform.bot_message import BotMessage, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform_metadata import PlatformMetadata


def test_getters_follow_the_bot_message() -> None:
    platform = make_inmemory("qq_main")
    event = platform.build_event(
        "hi", sender_id="42", sender_name="Bob", components=[At("verter"), Plain("hi")]
    )
    assert event.unified_msg_origin == "qq_main:FriendMessage:42"
    assert event.get_platform_name() == "inmemory"
    assert event.get_platform_id() == "qq_main"
    assert event.get_message_str() == "hi"
    assert event.get_message_outline() == "@verterhi"
    assert event.get_message_type() is MessageType.FRIEND_MESSAGE
    assert event.get_session_id() == "42"
    assert event.get_group_id() == ""
    assert event.get_self_id() == "verter"
    assert (event.get_sender_id(), event.get_sender_name()) == ("42", "Bob")
    assert event.is_private_chat()
    assert not event.is_admin()
    assert not event.is_wake_up()

    group = platform.build_event("hi", sender_id="42", group_id="g1")
    assert group.unified_msg_origin == "qq_main:GroupMessage:g1"
    assert group.get_group_id() == "g1"
    assert not group.is_private_chat()


def test_extras() -> None:
    event = make_inmemory().build_event("x")
    event.set_extra("k", 1)
    assert event.get_extra("k") == 1
    assert event.get_extra("missing", "d") == "d"
    event.clear_extra()
    assert event.get_extra("k") is None


def test_results_and_stop_semantics() -> None:
    event = make_inmemory().build_event("x")
    event.set_result("plain text")
    result = event.get_result()
    assert result is not None
    assert result.plain_text() == "plain text"
    assert not event.is_stopped()

    event.set_result(event.plain_result("bye").stop_event())
    assert event.is_stopped()
    event.continue_event()
    assert not event.is_stopped()
    event.stop_event()
    assert event.is_stopped()
    event.clear_result()
    assert event.get_result() is None

    assert isinstance(event.make_result(), MessageEventResult)
    image = event.image_result("https://x/a.png").chain[0]
    assert isinstance(image, Image)
    assert image.file == "https://x/a.png"
    local = event.image_result("images/a.png").chain[0]
    assert isinstance(local, Image)
    assert local.file.startswith("file://")
    assert event.chain_result([Plain("a")]).plain_text() == "a"

    event.should_call_llm(False)
    assert event.call_llm is False


async def test_send_marks_the_event() -> None:
    platform = make_inmemory()
    event = platform.build_event("x")
    assert not event.has_sent
    await event.send(MessageChain().message("pong"))
    assert event.has_sent
    sent = platform.sent.get_nowait()
    assert sent.session == event.unified_msg_origin
    assert sent.reply_to is event


async def test_base_event_cannot_send() -> None:
    message = BotMessage(
        type=MessageType.OTHER_MESSAGE,
        self_id="bot",
        session_id="s",
        message_id="1",
        sender=MessageMember("u"),
        message=[],
        message_str="",
    )
    event = MessageEvent("", message, PlatformMetadata("x", "x"), "s")
    with pytest.raises(NotImplementedError):
        await event.send(MessageChain())
