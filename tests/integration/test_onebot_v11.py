import asyncio
from collections.abc import Callable
from pathlib import Path

from tests.fakes.onebot import MockOneBotClient, MockOneBotServer, group_message, private_message
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle
from verter.core.message.components import At, Image, Plain
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.onebot_v11 import OneBotV11Platform


class _Sink:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []

    def commit(self, event: MessageEvent) -> bool:
        self.events.append(event)
        return True


async def _start_reverse(
    sink: _Sink, *, token: str = ""
) -> tuple[OneBotV11Platform, asyncio.Task[None]]:
    platform = OneBotV11Platform(
        PlatformConfig.model_validate(
            {
                "id": "qq",
                "type": "onebot_v11",
                "ws_reverse_host": "127.0.0.1",
                "ws_reverse_port": 0,
                "access_token": token,
            }
        ),
        PlatformContext(sink=sink, request_shutdown=lambda: None),
    )
    task = asyncio.create_task(platform.run())
    for _ in range(50):
        if platform.bound_port:
            return platform, task
        await asyncio.sleep(0.02)
    task.cancel()
    raise RuntimeError("reverse websocket did not bind")


async def test_reverse_ws_private_and_group_round_trip() -> None:
    sink = _Sink()
    platform, task = await _start_reverse(sink, token="s3cret")
    client = MockOneBotClient(f"ws://127.0.0.1:{platform.bound_port}/", token="s3cret")
    try:
        await client.connect()
        await client.send_event(private_message("/help"))
        await _until(lambda: len(sink.events) == 1)
        private = sink.events[0]
        assert private.message_str == "/help"
        assert private.get_sender_id() == "111"
        await private.send(MessageChain().message("pong").at("111"))
        action = await client.next_action()
        assert action["action"] == "send_msg"
        params = action["params"]
        assert isinstance(params, dict)
        assert params["message_type"] == "private"
        assert params["user_id"] == 111
        assert params["message"] == [
            {"type": "text", "data": {"text": "pong"}},
            {"type": "at", "data": {"qq": "111"}},
        ]

        await client.send_event(group_message(" hi"))
        await _until(lambda: len(sink.events) == 2)
        group = sink.events[1]
        assert not group.is_private_chat()
        assert any(isinstance(c, At) for c in group.message_obj.message)
        assert any(isinstance(c, Image) for c in group.message_obj.message)
        await group.send(
            MessageChain([Plain("ok"), At("111"), Image.from_url("https://example.test/out.png")])
        )
        group_action = await client.next_action()
        gparams = group_action["params"]
        assert isinstance(gparams, dict)
        assert gparams["message_type"] == "group"
        assert gparams["group_id"] == 333
        assert gparams["message"][0] == {"type": "text", "data": {"text": "ok"}}
        assert gparams["message"][1] == {"type": "at", "data": {"qq": "111"}}
        assert gparams["message"][2] == {
            "type": "image",
            "data": {"file": "https://example.test/out.png"},
        }
    finally:
        await client.close()
        await platform.terminate()
        await asyncio.wait_for(task, 2)


async def test_forward_ws_private_and_group_round_trip() -> None:
    server = MockOneBotServer()
    url = await server.start()
    sink = _Sink()
    platform = OneBotV11Platform(
        PlatformConfig.model_validate({"id": "qq", "type": "onebot_v11", "ws_forward_url": url}),
        PlatformContext(sink=sink, request_shutdown=lambda: None),
    )
    task = asyncio.create_task(platform.run())
    try:
        await server.push(private_message("/sid"))
        await _until(lambda: len(sink.events) == 1)
        await sink.events[0].send(MessageChain().message("sid-ok"))
        action = await server.next_action()
        assert action["action"] == "send_msg"
        await server.push(group_message("/help", at_qq="222"))
        await _until(lambda: len(sink.events) == 2)
        await sink.events[1].send(MessageChain().message("group-ok"))
        group_action = await server.next_action()
        params = group_action["params"]
        assert isinstance(params, dict)
        assert params["message_type"] == "group"
    finally:
        await platform.terminate()
        task.cancel()
        await server.close()


async def test_reverse_rejects_a_bad_token() -> None:
    sink = _Sink()
    platform, task = await _start_reverse(sink, token="right")
    client = MockOneBotClient(f"ws://127.0.0.1:{platform.bound_port}/", token="wrong")
    try:
        try:
            await client.connect()
            connected = True
        except Exception:  # noqa: BLE001 - mock NapCat should be refused
            connected = False
        if connected:
            await client.send_event(private_message())
            await asyncio.sleep(0.2)
        assert sink.events == []
    finally:
        await client.close()
        await platform.terminate()
        await asyncio.wait_for(task, 2)


async def test_console_commands_arrive_over_onebot(tmp_path: Path) -> None:
    config = VerterConfig()
    config.dashboard.enable = False
    config.computer.enable = False
    config.platform_settings.rate_limit.enable = False
    config.admins_id = ["111"]
    lifecycle = CoreLifecycle(
        config,
        data_dir=tmp_path,
        extra_platforms=[
            PlatformConfig.model_validate(
                {
                    "id": "qq",
                    "type": "onebot_v11",
                    "ws_reverse_host": "127.0.0.1",
                    "ws_reverse_port": 0,
                }
            )
        ],
    )
    await lifecycle.start()
    try:
        running = lifecycle.platforms.get("qq") if lifecycle.platforms else None
        assert isinstance(running, OneBotV11Platform)
        for _ in range(50):
            if running.bound_port:
                break
            await asyncio.sleep(0.02)
        assert running.bound_port
        client = MockOneBotClient(f"ws://127.0.0.1:{running.bound_port}/")
        await client.connect()
        try:
            await client.send_event(private_message("/help"))
            action = await client.next_action()
            params = action["params"]
            assert isinstance(params, dict)
            message = params["message"]
            assert isinstance(message, list)
            text = "".join(
                str(seg["data"]["text"])
                for seg in message
                if isinstance(seg, dict) and seg.get("type") == "text"
            )
            assert "/help" in text
            await client.send_event(group_message("hello", at_qq=None, image_url=None))
            await asyncio.sleep(0.3)
            assert len(client.actions) == 1
        finally:
            await client.close()
    finally:
        await lifecycle.stop()


async def _until(predicate: Callable[[], bool], wait_s: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition was not met")
