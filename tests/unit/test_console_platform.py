import io
import sys
from collections.abc import AsyncIterator

import pytest

from verter.core.config import ConfigError, PlatformConfig
from verter.core.message.components import At, Plain
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.console import ConsolePlatform, stdin_lines


class _Sink:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []

    def commit(self, event: MessageEvent) -> bool:
        self.events.append(event)
        return True


def _platform(
    sink: _Sink,
    *,
    lines: AsyncIterator[str] | None = None,
    write: list[str] | None = None,
    shutdown: list[str] | None = None,
    **settings: object,
) -> ConsolePlatform:
    config = PlatformConfig.model_validate({"id": "console", "type": "console", **settings})

    def source() -> AsyncIterator[str]:
        assert lines is not None
        return lines

    def stop() -> None:
        if shutdown is not None:
            shutdown.append("stop")

    return ConsolePlatform(
        config,
        PlatformContext(sink=sink, request_shutdown=stop),
        lines=None if lines is None else source,
        write=(lambda text: None) if write is None else write.append,
    )


async def _lines(*rows: str) -> AsyncIterator[str]:
    for row in rows:
        yield row


async def test_lines_become_events_and_eof_requests_shutdown() -> None:
    sink = _Sink()
    written: list[str] = []
    shutdown: list[str] = []
    platform = _platform(
        sink,
        lines=_lines("hello\n", " \n", "@verter ping"),
        write=written,
        shutdown=shutdown,
    )
    await platform.run()
    assert shutdown == ["stop"]
    assert len(sink.events) == 2
    assert sink.events[0].message_str == "hello"
    assert sink.events[0].get_message_type() is MessageType.FRIEND_MESSAGE
    mentioned = sink.events[1]
    assert mentioned.message_str == "ping"
    assert [type(part) for part in mentioned.get_messages()] == [At, Plain]
    assert written[0].startswith("[console] chatting as console")
    platform.print_reply(MessageChain().message("pong"))
    assert written[-1] == "verter> pong\n"
    await platform.send_by_session(
        MessageSession("console", MessageType.FRIEND_MESSAGE, "console"),
        MessageChain().message("direct"),
    )
    assert written[-1] == "verter> direct\n"


async def test_group_mode_and_exit_on_eof() -> None:
    sink = _Sink()
    shutdown: list[str] = []
    platform = _platform(
        sink,
        lines=_lines("@verter\n"),
        shutdown=shutdown,
        group_id="dev",
        sender_id="ada",
        exit_on_eof=False,
    )
    await platform.run()
    assert shutdown == []
    event = sink.events[0]
    assert event.get_message_type() is MessageType.GROUP_MESSAGE
    assert event.session_id == "dev"
    assert event.message_str == ""
    assert isinstance(event.get_messages()[0], At)


def test_unknown_settings_are_rejected() -> None:
    with pytest.raises(ConfigError, match="nope"):
        _platform(_Sink(), nope=True)


def test_default_writer_uses_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    platform = ConsolePlatform(
        PlatformConfig(id="console", type="console"),
        PlatformContext(sink=_Sink(), request_shutdown=lambda: None),
    )
    platform.print_reply(MessageChain().message("hi"))
    assert capsys.readouterr().out == "verter> hi\n"


async def test_stdin_lines_reads_until_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("one\ntwo\n"))
    assert [line async for line in stdin_lines()] == ["one\n", "two\n"]
