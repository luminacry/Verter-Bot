"""Telegram adapter tests against a fake Bot API server (no real bot token)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import orjson
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from verter.core.config import PlatformConfig
from verter.core.message.components import Image, Reply
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.telegram import (
    TelegramError,
    TelegramPlatform,
    _chunks,
    _utf16_len,
)

_TOKEN = "123:test-token"


class _FakeBotAPI:
    """Records Bot API calls and hands the test canned getUpdates responses."""

    def __init__(self) -> None:
        self.updates: asyncio.Queue[list[dict[str, object]]] = asyncio.Queue()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.offset_seen: int | None = None
        self.fail_get_me = False
        self.fail_next_get_updates = False
        self.uploads: list[str] = []
        self.reject_uploads = False
        self.rate_limit_once = False
        self.unauthorized = False
        self.drop_get_me = False

    async def handler(self, request: web.Request) -> web.Response:
        action = request.path.rsplit("/", 1)[-1]
        payload: dict[str, object] = {}
        if request.content_type == "application/json":
            raw = await request.read()
            try:
                parsed = orjson.loads(raw)
            except orjson.JSONDecodeError:
                parsed = {}
            payload = parsed if isinstance(parsed, dict) else {}
        elif request.content_type == "multipart/form-data":
            form = await request.post()
            payload = {key: value for key, value in form.items() if isinstance(value, str)}
            self.uploads.append(action)
        self.calls.append((action, payload))
        if action == "getMe":
            if self.drop_get_me:
                await asyncio.sleep(30)
            if self.fail_get_me:
                return web.json_response({"ok": False, "description": "nope"})
            return web.json_response({"ok": True, "result": {"id": 42, "username": "verter_bot"}})
        if action == "getUpdates":
            offset = payload.get("offset", 0)
            self.offset_seen = int(offset) if isinstance(offset, int) else 0
            if self.fail_next_get_updates:
                self.fail_next_get_updates = False
                return web.json_response({"ok": False, "description": "boom"}, status=500)
            # Long polling waits for updates instead of failing on an empty queue.
            try:
                updates = await asyncio.wait_for(self.updates.get(), 0.2)
            except TimeoutError:
                updates = []
            return web.json_response({"ok": True, "result": updates})
        if self.unauthorized:
            return web.json_response({"ok": False, "description": "nope"}, status=401)
        if self.rate_limit_once:
            self.rate_limit_once = False
            return web.json_response(
                {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 1}},
                status=429,
            )
        if action == "sendMessage":
            return web.json_response(
                {"ok": True, "result": {"message_id": 77, "chat": {"id": payload.get("chat_id")}}}
            )
        if action == "sendPhoto":
            if self.reject_uploads or payload.get("photo") == "https://bad.example/x.png":
                return web.json_response({"ok": False, "description": "bad photo"}, status=400)
            return web.json_response({"ok": True, "result": {"message_id": 78}})
        return web.json_response({"ok": True, "result": True})


async def _make(
    settings: dict[str, object] | None = None,
) -> tuple[TelegramPlatform, TestServer, _FakeBotAPI, asyncio.Task[None]]:
    fake = _FakeBotAPI()
    app = web.Application()
    app.router.add_route("*", "/bot{token}/{action}", fake.handler)
    server = TestServer(app)
    await server.start_server()
    config = PlatformConfig.model_validate(
        {
            "id": "tg",
            "type": "telegram",
            "token": _TOKEN,
            "api_base": str(server.make_url("")),
            "poll_timeout_s": 1,
            **(settings or {}),
        }
    )
    platform = TelegramPlatform(
        config, PlatformContext(sink=_Sink(), request_shutdown=lambda: None)
    )
    return platform, server, fake, asyncio.ensure_future(platform.run())


class _Sink:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []
        self._ready = asyncio.Event()

    def commit(self, event: MessageEvent) -> bool:
        self.events.append(event)
        self._ready.set()
        return True

    async def wait_one(self, wait_s: float = 2.0) -> MessageEvent:
        await asyncio.wait_for(self._ready.wait(), wait_s)
        self._ready.clear()
        return self.events[-1]


def _message(text: str, chat_type: str = "private", **extra: object) -> dict[str, object]:
    chat: dict[str, object] = (
        {"id": 999, "type": chat_type}
        if chat_type == "private"
        else {"id": 999, "type": chat_type, "title": "Test"}
    )
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 0,
            "chat": chat,
            "from": {"id": 555, "first_name": "Ada", "username": "ada"},
            "text": text,
            **extra,
        },
    }


async def test_get_me_failure_stops_the_adapter() -> None:
    _platform, server, fake, task = await _make()
    fake.fail_get_me = True
    try:
        with pytest.raises(Exception, match="nope"):
            await asyncio.wait_for(task, 2)
    finally:
        await server.close()


async def test_private_message_becomes_an_event() -> None:
    platform, server, fake, task = await _make()
    try:
        for _ in range(200):
            if platform.bot_username:
                break
            await asyncio.sleep(0.01)
        await fake.updates.put([_message("hello")])
        event = await _platform_event(platform, fake)
        assert event.get_message_type() is MessageType.FRIEND_MESSAGE
        assert event.message_str == "hello"
        assert event.get_sender_id() == "555"
        assert event.get_self_id() == "verter_bot"
        assert platform.bot_username == "verter_bot"
    finally:
        await platform.terminate()
        await task
        await server.close()


async def _platform_event(platform: TelegramPlatform, fake: _FakeBotAPI) -> MessageEvent:
    sink = platform.context.sink
    assert isinstance(sink, _Sink)
    return await sink.wait_one()


async def test_group_mention_splits_at_component() -> None:
    platform, server, fake, task = await _make()
    try:
        for _ in range(200):
            if platform.bot_username:
                break
            await asyncio.sleep(0.01)
        text = "@verter_bot hi there"
        await fake.updates.put(
            [
                _message(
                    text,
                    "group",
                    entities=[{"offset": 0, "length": len(text.split()[0]), "type": "mention"}],
                )
            ]
        )
        event = await _platform_event(platform, fake)
        assert event.get_message_type() is MessageType.GROUP_MESSAGE
        assert event.get_group_id() == "999"
        kinds = [type(c).__name__ for c in event.get_messages()]
        assert kinds[0] == "At"
        # The mention becomes its own component; message_str keeps only the plain text.
        assert event.message_str.strip() == "hi there"
        # The next poll acknowledges the update with offset = update_id + 1.
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            if fake.offset_seen == 2:
                break
            await asyncio.sleep(0.01)
        assert fake.offset_seen == 2
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_non_mention_entities_stay_plain() -> None:
    platform, server, fake, task = await _make()
    try:
        for _ in range(200):
            if platform.bot_username:
                break
            await asyncio.sleep(0.01)
        text = "@someone else and @not_bot"
        await fake.updates.put(
            [
                _message(
                    text,
                    "group",
                    entities=[
                        {"offset": 0, "length": 8, "type": "mention"},
                        {"offset": 18, "length": 8, "type": "mention"},
                    ],
                )
            ]
        )
        event = await _platform_event(platform, fake)
        kinds = [type(c).__name__ for c in event.get_messages()]
        assert kinds == ["Plain"]
        assert event.message_str == text
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_send_message_posts_to_the_api_with_interval() -> None:
    platform, server, fake, task = await _make(settings={"send_interval_s": 2.0})
    clock = {"now": 1000.0}
    slept: list[float] = []

    def fake_time() -> float:
        return clock["now"]

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    platform._clock = fake_time
    platform._sleep = fake_sleep
    try:
        await _wait_ready(platform)
        await platform.send_by_session(
            MessageSession("tg", MessageType.FRIEND_MESSAGE, "555"),
            MessageChain().message("hi").at("ada"),
        )
        await platform.send_by_session(
            MessageSession("tg", MessageType.FRIEND_MESSAGE, "555"),
            MessageChain().message("again"),
        )
        sends = [c for c in fake.calls if c[0] == "sendMessage"]
        assert len(sends) == 2
        assert sends[0][1]["chat_id"] == "555"
        assert sends[0][1]["text"] == "hi @ada"
        # The second send waits out the per-chat interval; no real time passes.
        assert slept == [2.0]
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_failed_get_updates_is_retried() -> None:
    platform, server, fake, task = await _make(settings={"retry_interval_s": 0.05})
    try:
        for _ in range(200):
            if platform.bot_username:
                break
            await asyncio.sleep(0.01)
        calls_before = len(fake.calls)
        # A getUpdates whose response is not ok must not kill the polling loop.
        fake.fail_next_get_updates = True
        await asyncio.sleep(0.15)
        assert not task.done()
        assert len(fake.calls) >= calls_before
    finally:
        await platform.terminate()
        await task
        await server.close()


def _session(chat: str = "555") -> MessageSession:
    return MessageSession("tg", MessageType.FRIEND_MESSAGE, chat)


async def _wait_ready(platform: TelegramPlatform) -> None:
    for _ in range(200):
        if platform.bot_username:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("adapter did not finish getMe")


async def test_reply_quote_and_utf16_mention_are_parsed() -> None:
    platform, server, fake, task = await _make()
    try:
        await _wait_ready(platform)
        text = "\U0001f600 @verter_bot hi"
        at_offset = len("\U0001f600 ".encode("utf-16-le")) // 2
        await fake.updates.put(
            [
                _message(
                    text,
                    "group",
                    entities=[{"offset": at_offset, "length": 11, "type": "mention"}],
                    reply_to_message={
                        "message_id": 9,
                        "from": {"id": 42, "username": "verter_bot"},
                    },
                )
            ]
        )
        event = await _platform_event(platform, fake)
        kinds = [type(c).__name__ for c in event.get_messages()]
        assert kinds[0] == "Reply"
        assert kinds[1] == "Plain"  # the emoji before the mention
        assert kinds[2] == "At"
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_message_without_text_is_ignored() -> None:
    platform, server, fake, task = await _make()
    try:
        await _wait_ready(platform)
        await fake.updates.put(
            [
                {
                    "update_id": 3,
                    "message": {
                        "message_id": 11,
                        "chat": {"id": 1, "type": "private"},
                        "from": {"id": 2},
                        "sticker": {"file_id": "s"},
                    },
                }
            ]
        )
        await asyncio.sleep(0.1)
        assert platform.context.sink.events == []  # type: ignore[attr-defined]
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_send_images_and_reply_quote() -> None:
    platform, server, fake, task = await _make(settings={"send_interval_s": 0.01})
    try:
        await _wait_ready(platform)
        chain = (
            MessageChain()
            .message("see")
            .url_image("https://example.com/a.png")
            .base64_image(b"\x89PNG")
        )
        chain.chain.insert(0, Reply("10"))
        await platform.send_by_session(_session(), chain)
        sent = [c for c in fake.calls if c[0] in ("sendMessage", "sendPhoto")]
        # Short text rides along as the first photo's caption instead of its own message.
        assert sent[0][0] == "sendPhoto"
        assert sent[0][1]["caption"] == "see"
        assert sent[0][1]["photo"] == "https://example.com/a.png"
        assert sent[0][1]["reply_to_message_id"] == "10"
        # The base64 image goes out as a multipart upload.
        assert fake.uploads == ["sendPhoto"]

        image = Path(__file__).with_name("telegram_image.png")
        image.write_bytes(b"\x89PNG")
        try:
            await platform.send_by_session(_session(), MessageChain().file_image(image))
        finally:
            image.unlink()
        assert fake.uploads == ["sendPhoto", "sendPhoto"]
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_bad_image_reference_and_rejected_photo_raise() -> None:
    platform, server, fake, task = await _make(settings={"send_interval_s": 0.01})
    try:
        await _wait_ready(platform)
        with pytest.raises(TelegramError, match="unsupported image reference"):
            await platform.send_by_session(_session(), MessageChain().append(Image("not-a-uri")))
        fake.reject_uploads = True
        with pytest.raises(TelegramError, match="bad photo"):
            await platform.send_by_session(_session(), MessageChain().base64_image(b"\x89PNG"))
    finally:
        await platform.terminate()
        await task
        await server.close()


def _command(text: str, length: int, target: str = "group") -> dict[str, object]:
    return _message(
        text,
        target,
        entities=[{"offset": 0, "length": length, "type": "bot_command"}],
    )


async def test_command_mention_is_stripped_only_for_this_bot() -> None:
    platform, server, fake, task = await _make()
    try:
        await _wait_ready(platform)
        await fake.updates.put([_command("/help@verter_bot", len("/help@verter_bot"))])
        event = await _platform_event(platform, fake)
        # The @botname suffix is gone, so the command index can match "help".
        assert event.message_str == "/help"

        await fake.updates.put([_command("/help@other_bot", len("/help@other_bot"))])
        await asyncio.sleep(0.3)
        # A command addressed to another bot never reaches the pipeline.
        assert len(platform.context.sink.events) == 1  # type: ignore[attr-defined]

        # A later @mention keeps its position even though the command suffix was removed.
        text = "/cmd@verter_bot @verter_bot hi"
        mention_at = len("/cmd@verter_bot ".encode("utf-16-le")) // 2
        await fake.updates.put(
            [
                _message(
                    text,
                    "group",
                    entities=[
                        {"offset": 0, "length": len("/cmd@verter_bot"), "type": "bot_command"},
                        {"offset": mention_at, "length": 11, "type": "mention"},
                    ],
                )
            ]
        )
        followed = await _platform_event(platform, fake)
        assert [type(c).__name__ for c in followed.get_messages()] == ["Plain", "At", "Plain"]
        assert followed.message_str.startswith("/cmd")
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_rate_limit_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    platform, server, fake, task = await _make(settings={"send_interval_s": 0.01})
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    platform._sleep = fake_sleep
    try:
        await _wait_ready(platform)
        fake.rate_limit_once = True
        await platform.send_by_session(_session(), MessageChain().message("hi"))
        sends = [c for c in fake.calls if c[0] == "sendMessage"]
        assert len(sends) == 2  # the 429, then the retry
        assert 1.0 in slept  # retry_after from the response
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_unauthorized_is_not_retried() -> None:
    platform, server, fake, task = await _make(settings={"send_interval_s": 0.01})
    try:
        await _wait_ready(platform)
        fake.unauthorized = True
        with pytest.raises(TelegramError, match="unauthorized"):
            await platform.send_by_session(_session(), MessageChain().message("hi"))
        assert len([c for c in fake.calls if c[0] == "sendMessage"]) == 1
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_failures_are_logged_without_the_token(caplog: pytest.LogCaptureFixture) -> None:
    platform, server, fake, task = await _make(settings={"retry_interval_s": 0.05})
    try:
        await _wait_ready(platform)
        with caplog.at_level("WARNING"):
            fake.fail_next_get_updates = True
            await asyncio.sleep(0.3)
        logged = " ".join(caplog.messages)
        # A 500 is retried, and the warning names the status without the bot token.
        assert "HTTP 500" in logged
        assert _TOKEN not in logged
    finally:
        await platform.terminate()
        await task
        await server.close()


async def test_get_me_network_failure_leaves_no_token_in_the_traceback() -> None:
    """A failed getMe must not chain the aiohttp error, whose URL holds the token."""
    _platform, server, fake, task = await _make(settings={"poll_timeout_s": 1})
    fake.drop_get_me = True
    try:
        with pytest.raises(TelegramError, match="getMe") as raised:
            await asyncio.wait_for(task, 10)
        error = raised.value
        assert _TOKEN not in str(error)
        assert error.__cause__ is None
    finally:
        await server.close()


def test_chunks_respect_the_utf16_limit_with_emoji() -> None:
    """Telegram counts text in UTF-16 units; emoji-heavy text must split under the limit."""
    emoji = "\U0001f600" * 3000 + " " + "a" * 10
    pieces = _chunks(emoji)
    assert all(_utf16_len(piece) <= 4096 for piece in pieces)
    assert "".join(pieces).replace(" ", "") == emoji.replace(" ", "")

    plain = "x" * 9000
    pieces = _chunks(plain)
    assert [_utf16_len(piece) for piece in pieces] == [4096, 4096, 808]

    # Breaks prefer newlines over hard cuts.
    lined = "line one\nline two " + "y" * 5000
    pieces = _chunks(lined)
    assert pieces[0] == "line one"
