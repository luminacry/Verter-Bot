"""A mock OneBot v11 implementation (NapCat-shaped) for tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from typing import Any, cast

from aiohttp import ClientSession, WSMsgType, web


def private_message(
    text: str = "/help",
    *,
    user_id: int = 111,
    self_id: int = 222,
    message_id: int = 1,
    extra: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    message: list[dict[str, object]] = [{"type": "text", "data": {"text": text}}]
    if extra:
        message.extend(extra)
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "time": 1,
        "self_id": self_id,
        "message_id": message_id,
        "user_id": user_id,
        "message": message,
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": "alice"},
    }


def group_message(
    text: str = "/help",
    *,
    user_id: int = 111,
    group_id: int = 333,
    self_id: int = 222,
    at_qq: str | None = "222",
    image_url: str | None = "https://example.test/cat.png",
    message_id: int = 2,
) -> dict[str, object]:
    message: list[dict[str, object]] = []
    if at_qq is not None:
        message.append({"type": "at", "data": {"qq": at_qq}})
    if text:
        message.append({"type": "text", "data": {"text": text}})
    if image_url is not None:
        message.append({"type": "image", "data": {"url": image_url}})
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": 1,
        "self_id": self_id,
        "message_id": message_id,
        "user_id": user_id,
        "group_id": group_id,
        "message": message,
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": "alice"},
    }


class MockOneBotClient:
    """Connects to a reverse WS server the way NapCat does."""

    def __init__(self, url: str, *, token: str = "") -> None:
        self.url = url
        self.token = token
        self.actions: list[dict[str, object]] = []
        self._incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._session: ClientSession | None = None
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        params = {"access_token": self.token} if self.token else None
        self._session = ClientSession()
        self._ws = await self._session.ws_connect(self.url, headers=headers, params=params)
        self._reader = asyncio.create_task(self._read())

    async def send_event(self, event: Mapping[str, object]) -> None:
        assert self._ws is not None
        await self._ws.send_str(json.dumps(event, ensure_ascii=False))

    async def next_action(self, wait_s: float = 2.0) -> dict[str, object]:
        return await asyncio.wait_for(self._incoming.get(), wait_s)

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _read(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type is not WSMsgType.TEXT:
                break
            payload = cast("dict[str, object]", json.loads(msg.data))
            self.actions.append(payload)
            echo = payload.get("echo")
            if echo is not None:
                await self._ws.send_str(
                    json.dumps(
                        {"status": "ok", "retcode": 0, "data": {"message_id": 9}, "echo": echo},
                        ensure_ascii=False,
                    )
                )
            await self._incoming.put(payload)


class MockOneBotServer:
    """Forward-mode peer: the adapter connects here."""

    def __init__(self) -> None:
        self.actions: list[dict[str, object]] = []
        self._incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._outgoing: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._runner: web.AppRunner | None = None
        self.port: int | None = None

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/", self._handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self._runner = runner
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.port = int(runner.addresses[0][1])
        return f"ws://127.0.0.1:{self.port}/"

    async def push(self, event: Mapping[str, object]) -> None:
        await self._outgoing.put(dict(event))

    async def next_action(self, wait_s: float = 2.0) -> dict[str, object]:
        return await asyncio.wait_for(self._incoming.get(), wait_s)

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sender = asyncio.create_task(self._pump_out(ws))
        try:
            async for msg in ws:
                if msg.type is WSMsgType.TEXT:
                    payload = cast("dict[str, object]", json.loads(msg.data))
                    self.actions.append(payload)
                    echo = payload.get("echo")
                    if echo is not None:
                        await ws.send_str(
                            json.dumps(
                                {
                                    "status": "ok",
                                    "retcode": 0,
                                    "data": {"message_id": 9},
                                    "echo": echo,
                                },
                                ensure_ascii=False,
                            )
                        )
                    await self._incoming.put(payload)
                else:
                    break
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
        return ws

    async def _pump_out(self, ws: web.WebSocketResponse) -> None:
        while True:
            event = await self._outgoing.get()
            await ws.send_str(json.dumps(event, ensure_ascii=False))
