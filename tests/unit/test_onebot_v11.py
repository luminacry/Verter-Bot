"""OneBot v11 adapter tests against a fake NapCat peer (no real QQ account)."""

from __future__ import annotations

import asyncio

import aiohttp
import orjson
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from verter.core.config import PlatformConfig
from verter.core.message.components import (
    At,
    Contact,
    Dice,
    File,
    Forward,
    Json,
    Mface,
    Music,
    Node,
    Plain,
    Poke,
    Reply,
    Rps,
    Video,
)
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.onebot_v11 import OneBotActionError, OneBotV11Platform

_PRIVATE_MESSAGE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "sub_type": "friend",
    "message_id": 1001,
    "user_id": 123456789,
    "message": [{"type": "text", "data": {"text": "hello"}}],
    "raw_message": "hello",
    "sender": {"user_id": 123456789, "nickname": "Ada"},
    "self_id": 10000,
}

_GROUP_MESSAGE_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "sub_type": "normal",
    "message_id": 2002,
    "group_id": 555,
    "user_id": 123456789,
    "message": [
        {"type": "at", "data": {"qq": "10000"}},
        {"type": "text", "data": {"text": " hi all"}},
    ],
    "raw_message": "[CQ:at,qq=10000] hi all",
    "sender": {"user_id": 123456789, "nickname": "Ada", "card": "AdaCard"},
    "self_id": 10000,
}


class _Sink:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []
        self._ready: asyncio.Event = asyncio.Event()

    def commit(self, event: MessageEvent) -> bool:
        self.events.append(event)
        self._ready.set()
        return True

    async def wait_one(self, wait_s: float = 2.0) -> MessageEvent:
        await asyncio.wait_for(self._ready.wait(), wait_s)
        self._ready.clear()
        return self.events[-1]


def _platform(sink: _Sink, **settings: object) -> OneBotV11Platform:
    config = PlatformConfig.model_validate({"id": "qq", "type": "onebot_v11", **settings})
    return OneBotV11Platform(config, PlatformContext(sink=sink, request_shutdown=lambda: None))


# -- reverse mode: verter is the WebSocket server, the test is the NapCat peer --------


async def test_reverse_mode_receives_private_and_group_messages() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            await ws.send_str(orjson.dumps(_PRIVATE_MESSAGE_EVENT).decode())
            event = await sink.wait_one()
            assert event.get_message_type() is MessageType.FRIEND_MESSAGE
            assert event.get_sender_id() == "123456789"
            assert event.get_sender_name() == "Ada"
            assert event.message_str == "hello"
            assert event.get_self_id() == "10000"

            await ws.send_str(orjson.dumps(_GROUP_MESSAGE_EVENT).decode())
            event = await sink.wait_one()
            assert event.get_message_type() is MessageType.GROUP_MESSAGE
            assert event.get_group_id() == "555"
            assert event.get_sender_name() == "AdaCard"
            assert [type(c) for c in event.get_messages()] == [At, Plain]
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_reverse_mode_rejects_bad_access_token() -> None:
    sink = _Sink()
    platform = _platform(
        sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0, access_token="secret"
    )
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.WSServerHandshakeError):
                async with session.ws_connect(f"ws://127.0.0.1:{port}/"):
                    pass
            async with session.ws_connect(
                f"ws://127.0.0.1:{port}/", headers={"Authorization": "Bearer secret"}
            ) as ws:
                await ws.send_str(orjson.dumps(_PRIVATE_MESSAGE_EVENT).decode())
                await sink.wait_one()
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_reverse_mode_send_by_session_calls_send_private_msg() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            await ws.send_str(orjson.dumps(_PRIVATE_MESSAGE_EVENT).decode())
            await sink.wait_one()

            send = asyncio.ensure_future(
                platform.send_by_session(
                    MessageSession("qq", MessageType.FRIEND_MESSAGE, "123456789"),
                    MessageChain().message("hi back").at("123456789"),
                )
            )
            raw = await asyncio.wait_for(ws.receive_str(), 2)
            request = orjson.loads(raw)
            assert request["action"] == "send_private_msg"
            assert request["params"]["user_id"] == "123456789"
            assert request["params"]["message"] == [
                {"type": "text", "data": {"text": "hi back"}},
                {"type": "at", "data": {"qq": "123456789"}},
            ]
            await ws.send_str(
                orjson.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"message_id": 1},
                        "echo": request["echo"],
                    }
                ).decode()
            )
            await asyncio.wait_for(send, 2)
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_failed_action_raises_and_a_dead_connection_does_not_fail_the_new_one() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with aiohttp.ClientSession() as session:
            old = await session.ws_connect(f"ws://127.0.0.1:{port}/")
            new = await session.ws_connect(f"ws://127.0.0.1:{port}/")

            failed = asyncio.ensure_future(platform.call("set_group_ban", {"group_id": "1"}))
            request = orjson.loads(await asyncio.wait_for(new.receive_str(), 2))
            await new.send_str(
                orjson.dumps(
                    {
                        "status": "failed",
                        "retcode": 1400,
                        "message": "denied",
                        "echo": request["echo"],
                    }
                ).decode()
            )
            with pytest.raises(OneBotActionError, match="denied"):
                await asyncio.wait_for(failed, 2)

            pending = asyncio.ensure_future(platform.call("get_status"))
            await old.close()
            request = orjson.loads(await asyncio.wait_for(new.receive_str(), 2))
            await new.send_str(
                orjson.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"online": True},
                        "echo": request["echo"],
                    }
                ).decode()
            )
            assert await asyncio.wait_for(pending, 2) == {"online": True}
            await new.close()
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_reply_quote_resolves_the_quoted_sender() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            quoted = {
                **_GROUP_MESSAGE_EVENT,
                "message": [{"type": "reply", "data": {"id": "2002"}}],
            }
            await ws.send_str(orjson.dumps(quoted).decode())
            request = orjson.loads(await asyncio.wait_for(ws.receive_str(), 2))
            assert request["action"] == "get_msg"
            assert request["params"]["message_id"] == "2002"
            await ws.send_str(
                orjson.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"sender": {"user_id": 10000}},
                        "echo": request["echo"],
                    }
                ).decode()
            )
            event = await sink.wait_one()
            reply = event.get_messages()[0]
            assert isinstance(reply, Reply)
            assert reply.sender_id == "10000"
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_reverse_mode_rejects_non_array_message_format() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            bad = {**_PRIVATE_MESSAGE_EVENT, "message": "hello"}
            await ws.send_str(orjson.dumps(bad).decode())
            # Give the dispatch loop a turn; no event should ever arrive.
            with pytest.raises(TimeoutError):
                await sink.wait_one(wait_s=0.2)
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def _wait_for_port(platform: OneBotV11Platform, wait_s: float = 2.0) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        port = platform.bound_port
        if port is not None:
            return port
        await asyncio.sleep(0.01)
    raise TimeoutError("reverse WebSocket server did not start")


# -- forward mode: verter is the WebSocket client, the test runs a fake NapCat server --


class _FakeNapCatServer:
    """Accepts one connection and lets the test drive it like NapCat would."""

    def __init__(self) -> None:
        self.ws: web.WebSocketResponse | None = None
        self.connected = asyncio.Event()
        self.received: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws = ws
        self.connected.set()
        async for raw in ws:
            if raw.type is aiohttp.WSMsgType.TEXT:
                self.received.put_nowait(orjson.loads(raw.data))
        return ws


async def test_forward_mode_connects_and_receives_messages() -> None:
    fake = _FakeNapCatServer()
    app = web.Application()
    app.router.add_get("/", fake.handler)
    server = TestServer(app)
    await server.start_server()
    try:
        sink = _Sink()
        platform = _platform(sink, mode="ws_forward", ws_forward_url=str(server.make_url("/")))
        run_task = asyncio.ensure_future(platform.run())
        try:
            await asyncio.wait_for(fake.connected.wait(), 2)
            assert fake.ws is not None
            await fake.ws.send_str(orjson.dumps(_PRIVATE_MESSAGE_EVENT).decode())
            event = await sink.wait_one()
            assert event.message_str == "hello"
        finally:
            await platform.terminate()
            await asyncio.wait_for(run_task, 2)
    finally:
        await server.close()


async def test_forward_mode_sends_access_token_header() -> None:
    fake = _FakeNapCatServer()
    seen_auth: list[str] = []

    async def handler(request: web.Request) -> web.WebSocketResponse:
        seen_auth.append(request.headers.get("Authorization", ""))
        return await fake.handler(request)

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        sink = _Sink()
        platform = _platform(
            sink,
            mode="ws_forward",
            ws_forward_url=str(server.make_url("/")),
            access_token="tok",
        )
        run_task = asyncio.ensure_future(platform.run())
        try:
            await asyncio.wait_for(fake.connected.wait(), 2)
            assert seen_auth == ["Bearer tok"]
        finally:
            await platform.terminate()
            await asyncio.wait_for(run_task, 2)
    finally:
        await server.close()


async def test_forward_mode_reconnects_after_disconnect() -> None:
    fake = _FakeNapCatServer()
    connect_count = 0

    async def handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal connect_count
        connect_count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if connect_count == 1:
            await ws.close()
            return ws
        fake.ws = ws
        fake.connected.set()
        async for _raw in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        sink = _Sink()
        platform = _platform(
            sink,
            mode="ws_forward",
            ws_forward_url=str(server.make_url("/")),
            reconnect_interval_s=0.05,
        )
        run_task = asyncio.ensure_future(platform.run())
        try:
            await asyncio.wait_for(fake.connected.wait(), 2)
            assert connect_count >= 2
        finally:
            await platform.terminate()
            await asyncio.wait_for(run_task, 2)
    finally:
        await server.close()


_RICH_SEGMENTS = [
    {"type": "video", "data": {"file": "v.mp4", "url": "http://x/v.mp4"}},
    {"type": "file", "data": {"name": "a.zip", "file": "fid"}},
    {"type": "json", "data": {"data": "{}"}},
    {"type": "forward", "data": {"id": "fwd1"}},
    {"type": "music", "data": {"type": "163", "id": "9"}},
    {"type": "contact", "data": {"type": "qq", "id": "42"}},
    {"type": "poke", "data": {"qq": "7"}},
    {"type": "dice", "data": {"result": "3"}},
    {"type": "rps", "data": {"result": "1"}},
    {"type": "mface", "data": {"emoji_id": "e", "summary": "cat"}},
]


async def test_rich_segments_convert_both_ways() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            event = {**_GROUP_MESSAGE_EVENT, "message": _RICH_SEGMENTS}
            await ws.send_str(orjson.dumps(event).decode())
            received = await sink.wait_one()
            kinds = [type(c) for c in received.get_messages()]
            assert kinds == [Video, File, Json, Forward, Music, Contact, Poke, Dice, Rps, Mface]
            assert received.get_messages()[0].file == "http://x/v.mp4"  # type: ignore[attr-defined]

            send = asyncio.ensure_future(
                platform.send_by_session(
                    MessageSession("qq", MessageType.GROUP_MESSAGE, "555"),
                    MessageChain(
                        [
                            Video("v.mp4"),
                            File("a.zip", "fid"),
                            Json("{}"),
                            Forward("fwd1"),
                            Node(content=[Plain("x")], user_id="1", nickname="Ada"),
                            Music("custom", url="http://m", title="song"),
                            Contact("group", "99"),
                            Poke("7"),
                            Dice("3"),
                            Rps(),
                            Mface("e", "p", "k", "cat"),
                        ]
                    ),
                )
            )
            request = orjson.loads(await asyncio.wait_for(ws.receive_str(), 2))
            assert request["action"] == "send_group_msg"
            assert [seg["type"] for seg in request["params"]["message"]] == [
                "video",
                "file",
                "json",
                "forward",
                "node",
                "music",
                "contact",
                "poke",
                "dice",
                "rps",
                "mface",
            ]
            node = request["params"]["message"][4]["data"]
            assert node["content"] == [{"type": "text", "data": {"text": "x"}}]
            assert node["user_id"] == "1"
            await ws.send_str(
                orjson.dumps(
                    {"status": "ok", "retcode": 0, "data": {}, "echo": request["echo"]}
                ).decode()
            )
            await asyncio.wait_for(send, 2)
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_notice_and_request_events_reach_the_pipeline() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            await ws.send_str(
                orjson.dumps(
                    {
                        "post_type": "notice",
                        "notice_type": "group_ban",
                        "sub_type": "ban",
                        "group_id": 555,
                        "user_id": 7,
                        "operator_id": 1,
                        "duration": 60,
                        "self_id": 10000,
                    }
                ).decode()
            )
            notice = await sink.wait_one()
            assert notice.get_message_type() is MessageType.NOTICE
            assert notice.message_str == "group_ban.ban"
            assert notice.get_group_id() == "555"
            assert notice.get_sender_id() == "7"

            await ws.send_str(
                orjson.dumps(
                    {
                        "post_type": "request",
                        "request_type": "friend",
                        "user_id": 9,
                        "flag": "f1",
                        "comment": "hi",
                        "self_id": 10000,
                    }
                ).decode()
            )
            request = await sink.wait_one()
            assert request.get_message_type() is MessageType.REQUEST
            assert request.message_str == "friend"
            assert request.session_id == "9"
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_heartbeat_and_lifecycle_track_online_state() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        assert not platform.online
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):

            async def send_meta(**fields: object) -> None:
                await ws.send_str(
                    orjson.dumps({"post_type": "meta_event", "self_id": 10000, **fields}).decode()
                )

            async def until(predicate: object) -> None:
                for _ in range(200):
                    if callable(predicate) and predicate():
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("condition not reached")

            await until(lambda: platform.connected)
            assert platform.online  # connected, nothing reported yet
            assert platform.last_heartbeat is None

            await send_meta(meta_event_type="heartbeat", status={"online": False}, interval=30000)
            await until(lambda: platform.last_heartbeat is not None)
            assert not platform.online

            await send_meta(meta_event_type="lifecycle", sub_type="enable")
            await until(lambda: platform.online)

            await send_meta(meta_event_type="lifecycle", sub_type="disable")
            await until(lambda: not platform.online)
        # Meta events never reach the pipeline.
        assert sink.events == []
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


async def test_typed_wrappers_send_the_right_actions() -> None:
    sink = _Sink()
    platform = _platform(sink, ws_reverse_host="127.0.0.1", ws_reverse_port=0)
    run_task = asyncio.ensure_future(platform.run())
    try:
        port = await _wait_for_port(platform)
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            for _ in range(200):
                if platform.connected:
                    break
                await asyncio.sleep(0.01)

            async def answer(data: object) -> dict[str, object]:
                request = orjson.loads(await asyncio.wait_for(ws.receive_str(), 2))
                await ws.send_str(
                    orjson.dumps(
                        {"status": "ok", "retcode": 0, "data": data, "echo": request["echo"]}
                    ).decode()
                )
                return {"action": request["action"], "params": request["params"]}

            ban = asyncio.ensure_future(platform.set_group_ban("555", "7", duration_s=60))
            assert await answer(None) == {
                "action": "set_group_ban",
                "params": {"group_id": "555", "user_id": "7", "duration": 60},
            }
            assert await asyncio.wait_for(ban, 2) is None

            members = asyncio.ensure_future(platform.get_group_member_list("555"))
            seen = await answer([{"user_id": 7}, "junk", {"user_id": 8}])
            assert seen["action"] == "get_group_member_list"
            assert await asyncio.wait_for(members, 2) == [{"user_id": 7}, {"user_id": 8}]

            info = asyncio.ensure_future(platform.get_login_info())
            assert (await answer({"user_id": 10000}))["action"] == "get_login_info"
            assert await asyncio.wait_for(info, 2) == {"user_id": 10000}

            recall = asyncio.ensure_future(platform.delete_msg("42"))
            assert await answer(None) == {"action": "delete_msg", "params": {"message_id": "42"}}
            await asyncio.wait_for(recall, 2)

            join = asyncio.ensure_future(
                platform.set_group_add_request("f1", "add", approve=False, reason="no")
            )
            assert await answer(None) == {
                "action": "set_group_add_request",
                "params": {"flag": "f1", "sub_type": "add", "approve": False, "reason": "no"},
            }
            await asyncio.wait_for(join, 2)

            # A dict-returning wrapper tolerates a non-dict payload.
            group = asyncio.ensure_future(platform.get_group_info("555"))
            await answer(None)
            assert await asyncio.wait_for(group, 2) == {}
    finally:
        await platform.terminate()
        await asyncio.wait_for(run_task, 2)


def test_unknown_settings_key_is_rejected() -> None:
    with pytest.raises(Exception, match="nope"):
        _platform(_Sink(), nope=True)
