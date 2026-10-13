from pathlib import Path

import pytest

from verter.core.message.components import (
    At,
    AtAll,
    Face,
    File,
    Image,
    Plain,
    Record,
    Reply,
    Unknown,
)
from verter.core.message.message_event_result import MessageChain, MessageEventResult
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType


def test_component_outlines() -> None:
    parts = [
        Plain("hi "),
        At("42", "Bob"),
        At("43"),
        AtAll(),
        Image.from_url("https://x/y.png"),
        Record("file:///a.silk"),
        File("report.pdf"),
        Reply("m1"),
        Face("14"),
        Unknown("json", {"k": 1}),
    ]
    assert [p.outline() for p in parts] == [
        "hi ",
        "@Bob",
        "@43",
        "@all",
        "[image]",
        "[voice]",
        "[file:report.pdf]",
        "[reply]",
        "[face:14]",
        "[json]",
    ]


def test_media_constructors(tmp_path: Path) -> None:
    picture = tmp_path / "a.png"
    assert Image.from_path(picture).file == picture.resolve().as_uri()
    assert Image.from_base64(b"\x00\x01").file == "base64://AAE="
    assert Image.from_base64("QUJD").file == "base64://QUJD"
    assert Record.from_path(picture).file.startswith("file://")


def test_chain_builders_and_text() -> None:
    chain = (
        MessageChain()
        .message("hello ")
        .at("42", "Bob")
        .at_all()
        .url_image("https://x/y.png")
        .base64_image("QUJD")
        .append(Plain("!"))
    )
    assert len(chain) == 6
    assert bool(chain)
    assert chain.plain_text() == chain.get_plain_text() == "hello !"
    assert chain.outline() == "hello @Bob@all[image][image]!"
    assert [type(c).__name__ for c in chain] == ["Plain", "At", "AtAll", "Image", "Image", "Plain"]
    assert chain == MessageChain(list(chain))
    assert chain != MessageChain()
    assert chain != "hello"
    assert repr(MessageChain([Plain("x")])) == "MessageChain([Plain(text='x')])"
    assert not MessageChain()


def test_file_image_uses_a_file_uri(tmp_path: Path) -> None:
    chain = MessageChain().file_image(tmp_path / "pic.jpg")
    image = chain.chain[0]
    assert isinstance(image, Image)
    assert image.file.startswith("file://")


def test_result_stop_flags() -> None:
    result = MessageEventResult().message("x")
    assert not result.is_stopped()
    assert result.stop_event() is result
    assert result.is_stopped()
    assert not result.continue_event().is_stopped()


def test_message_session_round_trip() -> None:
    session = MessageSession("qq", MessageType.GROUP_MESSAGE, "123:abc")
    assert str(session) == "qq:GroupMessage:123:abc"
    assert MessageSession.from_str(str(session)) == session


@pytest.mark.parametrize("origin", ["", "qq", "qq:GroupMessage", ":GroupMessage:1", "qq:Bad:1"])
def test_message_session_rejects_malformed_origins(origin: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - enum and format errors share the type
        MessageSession.from_str(origin)
