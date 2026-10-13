import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path

from tests.fakes.telegram import MockTelegram, group_update, private_update
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle
from verter.core.message.components import Image, Plain
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.telegram import TelegramPlatform


class _Sink:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []

    def commit(self, event: MessageEvent) -> bool:
        self.events.append(event)
        return True


async def _start(api_base: str, sink: _Sink) -> tuple[TelegramPlatform, asyncio.Task[None]]:
    platform = TelegramPlatform(
        PlatformConfig.model_validate(
            {
                "id": "tg",
                "type": "telegram",
                "token": "123:abc",
                "api_base": api_base,
                "poll_timeout_s": 1,
                "throttle_s": 0,
            }
        ),
        PlatformContext(sink=sink, request_shutdown=lambda: None),
    )
    task = asyncio.create_task(platform.run())
    return platform, task


async def test_private_and_group_round_trip() -> None:
    server = MockTelegram()
    await server.start()
    sink = _Sink()
    platform, task = await _start(server.api_base, sink)
    try:
        await server.push(private_update("/help", mention="@bot"))
        await _until(lambda: len(sink.events) == 1)
        private = sink.events[0]
        assert private.is_private_chat()
        assert private.message_str == "/help @bot" or "/help" in private.message_str
        await private.send(MessageChain().message("pong").at("111", "Ada"))
        await _until(lambda: any(item.get("method") == "sendMessage" for item in server.sent))
        sent = next(item for item in server.sent if item.get("method") == "sendMessage")
        assert sent["chat_id"] == 111
        assert "pong" in str(sent["text"])

        await server.push(group_update("/help"))
        await _until(lambda: len(sink.events) == 2)
        group = sink.events[1]
        assert not group.is_private_chat()
        assert any(isinstance(c, Image) for c in group.message_obj.message)
        await group.send(MessageChain([Plain("pic"), Image.from_url("https://example.test/a.png")]))
        await _until(lambda: any(item.get("method") == "sendPhoto" for item in server.sent))
        photo = next(item for item in server.sent if item.get("method") == "sendPhoto")
        assert photo["chat_id"] == -100333
        assert photo["photo"] == "https://example.test/a.png"
        assert photo["caption"] == "pic"
    finally:
        await platform.terminate()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await server.close()


async def test_getupdates_retries_after_failure() -> None:
    server = MockTelegram()
    server.fail_updates = 1
    await server.start()
    sink = _Sink()
    platform, task = await _start(server.api_base, sink)
    try:
        await server.push(private_update("/sid"))
        await _until(lambda: len(sink.events) == 1, wait_s=4)
        assert sink.events[0].message_str == "/sid"
    finally:
        await platform.terminate()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await server.close()


async def test_unwoken_group_text_is_ignored(tmp_path: Path) -> None:
    server = MockTelegram()
    await server.start()
    config = VerterConfig()
    config.dashboard.enable = False
    config.computer.enable = False
    config.platform_settings.rate_limit.enable = False
    lifecycle = CoreLifecycle(
        config,
        data_dir=tmp_path,
        extra_platforms=[
            PlatformConfig.model_validate(
                {
                    "id": "tg",
                    "type": "telegram",
                    "token": "123:abc",
                    "api_base": server.api_base,
                    "poll_timeout_s": 1,
                    "throttle_s": 0,
                }
            )
        ],
    )
    await lifecycle.start()
    try:
        await server.push(group_update("hello", photo_id=None))
        await asyncio.sleep(0.4)
        assert server.sent == []
        await server.push(private_update("/help"))
        await _until(lambda: any(item.get("method") == "sendMessage" for item in server.sent))
        sent = next(item for item in server.sent if item.get("method") == "sendMessage")
        text = str(sent["text"])
        assert "/help" in text
    finally:
        await lifecycle.stop()
        await server.close()


async def _until(predicate: Callable[[], bool], wait_s: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition was not met")
