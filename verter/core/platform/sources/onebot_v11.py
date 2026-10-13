"""OneBot v11 adapter: reverse WebSocket server and forward WebSocket client."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
from collections.abc import Mapping, Sequence
from typing import cast

from aiohttp import (
    ClientSession,
    ClientWebSocketResponse,
    WSCloseCode,
    WSMsgType,
    web,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from verter.core.config import ConfigError, PlatformConfig
from verter.core.log import logger
from verter.core.message.components import (
    At,
    AtAll,
    Face,
    Image,
    MessageComponent,
    Plain,
    Record,
    Reply,
    Unknown,
)
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata

_SEND_TIMEOUT_S = 10.0


class OneBotSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ws_reverse_host: str | None = None
    ws_reverse_port: int | None = Field(default=None, ge=0, le=65535)
    ws_forward_url: str | None = None
    access_token: str = ""

    @model_validator(mode="after")
    def _need_a_transport(self) -> OneBotSettings:
        reverse = self.ws_reverse_host is not None or self.ws_reverse_port is not None
        if reverse and (self.ws_reverse_host is None or self.ws_reverse_port is None):
            raise ValueError("ws_reverse_host and ws_reverse_port must be set together")
        if not reverse and not self.ws_forward_url:
            raise ValueError("set ws_reverse_host/ws_reverse_port or ws_forward_url")
        return self


def resolve_secret(value: str) -> str:
    """Expand ``ENV:NAME`` to the matching environment variable, otherwise return ``value``."""
    if value.startswith("ENV:"):
        return os.environ.get(value[4:], "")
    return value


def segments_from_onebot(raw: object, *, fallback: str = "") -> list[MessageComponent]:
    """Turn a OneBot v11 ``message`` field into Verter segments."""
    if isinstance(raw, str):
        return [Plain(raw)] if raw else ([Plain(fallback)] if fallback else [])
    if not isinstance(raw, list):
        return [Plain(fallback)] if fallback else []
    parts: list[MessageComponent] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        mapping = cast("dict[str, object]", item)
        kind = mapping.get("type")
        data_obj = mapping.get("data")
        data: dict[str, object] = (
            cast("dict[str, object]", data_obj) if isinstance(data_obj, dict) else {}
        )
        if kind == "text":
            parts.append(Plain(str(data.get("text", ""))))
        elif kind == "at":
            qq = str(data.get("qq", ""))
            if qq == "all":
                parts.append(AtAll())
            else:
                parts.append(At(qq, str(data.get("name", ""))))
        elif kind == "image":
            parts.append(Image(str(data.get("url") or data.get("file") or "")))
        elif kind == "record":
            parts.append(Record(str(data.get("url") or data.get("file") or "")))
        elif kind == "reply":
            parts.append(Reply(str(data.get("id", "")), text=str(data.get("text", ""))))
        elif kind == "face":
            parts.append(Face(str(data.get("id", ""))))
        else:
            parts.append(Unknown(str(kind or "unknown"), dict(data)))
    return parts or ([Plain(fallback)] if fallback else [])


def segments_to_onebot(chain: Sequence[MessageComponent]) -> list[dict[str, object]]:
    """Turn Verter segments into OneBot v11 message arrays."""
    out: list[dict[str, object]] = []
    for part in chain:
        if isinstance(part, Plain):
            out.append({"type": "text", "data": {"text": part.text}})
        elif isinstance(part, AtAll):
            out.append({"type": "at", "data": {"qq": "all"}})
        elif isinstance(part, At):
            data: dict[str, object] = {"qq": part.target}
            if part.name:
                data["name"] = part.name
            out.append({"type": "at", "data": data})
        elif isinstance(part, Image):
            out.append({"type": "image", "data": {"file": part.file}})
        elif isinstance(part, Record):
            out.append({"type": "record", "data": {"file": part.file}})
        elif isinstance(part, Reply):
            out.append({"type": "reply", "data": {"id": part.message_id}})
        elif isinstance(part, Face):
            out.append({"type": "face", "data": {"id": part.id}})
        elif isinstance(part, Unknown):
            out.append({"type": part.kind, "data": dict(part.data)})
        else:
            out.append({"type": "text", "data": {"text": part.outline()}})
    return out


class OneBotEvent(MessageEvent):
    __slots__ = ("_platform",)

    def __init__(
        self,
        message_str: str,
        message_obj: BotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        platform: OneBotV11Platform,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._platform = platform

    async def _send(self, message: MessageChain) -> None:
        await self._platform.send_chain(self.message_obj, message)


class OneBotV11Platform(Platform):
    """NapCat-oriented OneBot v11 adapter. HTTP POST is not implemented."""

    def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
        super().__init__(config, context)
        self.settings = self.parse_settings(OneBotSettings)
        self._token = resolve_secret(self.settings.access_token)
        self._meta = PlatformMetadata("onebot_v11", config.id, "OneBot v11 (QQ / NapCat)")
        self._closed = asyncio.Event()
        self._ws: ClientWebSocketResponse | web.WebSocketResponse | None = None
        self._ws_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Mapping[str, object]]] = {}
        self._echoes = itertools.count(1)
        self._self_id = ""
        self._runner: web.AppRunner | None = None
        self._session: ClientSession | None = None
        self.bound_port: int | None = None

    def meta(self) -> PlatformMetadata:
        return self._meta

    async def run(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        if self.settings.ws_reverse_host is not None and self.settings.ws_reverse_port is not None:
            tasks.append(
                asyncio.create_task(self._run_reverse(), name=f"onebot-reverse:{self.config.id}")
            )
        if self.settings.ws_forward_url:
            tasks.append(
                asyncio.create_task(self._run_forward(), name=f"onebot-forward:{self.config.id}")
            )
        if not tasks:
            raise ConfigError("onebot_v11 has no transport")
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            await self._cleanup()

    async def terminate(self) -> None:
        self._closed.set()
        await self._cleanup()

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        bot = BotMessage(
            type=session.message_type,
            self_id=self._self_id,
            session_id=session.session_id,
            message_id="",
            sender=MessageMember(session.session_id),
            message=list(message.chain),
            message_str=message.plain_text(),
            group=(
                Group(session.session_id)
                if session.message_type is MessageType.GROUP_MESSAGE
                else None
            ),
        )
        await self.send_chain(bot, message)

    async def send_chain(self, incoming: BotMessage, message: MessageChain) -> None:
        params: dict[str, object] = {"message": segments_to_onebot(message.chain)}
        if incoming.type is MessageType.GROUP_MESSAGE:
            params["message_type"] = "group"
            params["group_id"] = _as_id(incoming.group_id)
        else:
            params["message_type"] = "private"
            params["user_id"] = _as_id(incoming.sender.user_id)
        reply = await self._call("send_msg", params)
        status = str(reply.get("status", ""))
        retcode = reply.get("retcode", 1)
        if status != "ok" or retcode not in (0, "0"):
            raise RuntimeError(f"onebot send_msg failed: {reply}")

    def event_from_payload(self, payload: Mapping[str, object]) -> OneBotEvent | None:
        """Build an event from a OneBot v11 frame. Returns None for non-message posts."""
        if payload.get("post_type") != "message":
            self_id = payload.get("self_id")
            if self_id is not None:
                self._self_id = str(self_id)
            return None
        message_type = str(payload.get("message_type", ""))
        sender_obj = payload.get("sender")
        sender_map: dict[str, object] = (
            cast("dict[str, object]", sender_obj) if isinstance(sender_obj, dict) else {}
        )
        user_id = str(payload.get("user_id") or sender_map.get("user_id") or "")
        group_id = str(payload.get("group_id", "")) if message_type == "group" else ""
        raw_message = str(payload.get("raw_message", ""))
        components = segments_from_onebot(payload.get("message"), fallback=raw_message)
        message_str = "".join(c.text for c in components if isinstance(c, Plain)) or raw_message
        kind = MessageType.GROUP_MESSAGE if message_type == "group" else MessageType.FRIEND_MESSAGE
        session_id = group_id if kind is MessageType.GROUP_MESSAGE else user_id
        self_id = str(payload.get("self_id", self._self_id))
        self._self_id = self_id
        bot_message = BotMessage(
            type=kind,
            self_id=self_id,
            session_id=session_id,
            message_id=str(payload.get("message_id", "")),
            sender=MessageMember(user_id, str(sender_map.get("nickname", ""))),
            message=components,
            message_str=message_str,
            group=Group(group_id) if group_id else None,
            raw_message=dict(payload),
        )
        return OneBotEvent(message_str, bot_message, self._meta, session_id, self)

    async def _run_reverse(self) -> None:
        host = self.settings.ws_reverse_host
        port = self.settings.ws_reverse_port
        if host is None or port is None:
            raise ConfigError("reverse websocket host and port are required")
        app = web.Application()
        app.router.add_get("/", self._reverse_handler)
        app.router.add_get("/ws", self._reverse_handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self._runner = runner
        site = web.TCPSite(runner, host, port)
        await site.start()
        addresses = runner.addresses
        if addresses:
            self.bound_port = int(addresses[0][1])
        logger.info("OneBot reverse WS listening on %s:%s", host, self.bound_port)
        await self._closed.wait()

    async def _reverse_handler(self, request: web.Request) -> web.StreamResponse:
        if not _authorized(request, self._token):
            return web.Response(status=401, text="unauthorized")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        await self._use_socket(ws)
        return ws

    async def _run_forward(self) -> None:
        url = self.settings.ws_forward_url
        if url is None:
            raise ConfigError("ws_forward_url is required")
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        params = {"access_token": self._token} if self._token else None
        session = ClientSession()
        self._session = session
        ws = await session.ws_connect(url, headers=headers, params=params, heartbeat=20)
        await self._use_socket(ws)
        if not self._closed.is_set():
            raise ConnectionError("onebot forward websocket closed")

    async def _use_socket(self, ws: ClientWebSocketResponse | web.WebSocketResponse) -> None:
        async with self._ws_lock:
            self._ws = ws
        try:
            async for msg in ws:
                if self._closed.is_set():
                    break
                if msg.type is WSMsgType.TEXT:
                    await self._on_text(msg.data)
                elif msg.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        finally:
            async with self._ws_lock:
                if self._ws is ws:
                    self._ws = None
            self._fail_pending("onebot websocket closed")
            if not ws.closed:
                await ws.close(code=WSCloseCode.GOING_AWAY)

    async def _on_text(self, data: str) -> None:
        try:
            payload_obj: object = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("OneBot %s received invalid JSON", self.config.id)
            return
        if not isinstance(payload_obj, dict):
            return
        payload = cast("dict[str, object]", payload_obj)
        echo = payload.get("echo")
        if echo is not None and "post_type" not in payload:
            future = self._pending.pop(str(echo), None)
            if future is not None and not future.done():
                future.set_result(payload)
            return
        event = self.event_from_payload(payload)
        if event is not None:
            self.commit_event(event)

    async def _call(self, action: str, params: dict[str, object]) -> Mapping[str, object]:
        echo = str(next(self._echoes))
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, object]] = loop.create_future()
        self._pending[echo] = future
        body = json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False)
        async with self._ws_lock:
            ws = self._ws
            if ws is None or ws.closed:
                self._pending.pop(echo, None)
                raise RuntimeError("onebot websocket is not connected")
            await ws.send_str(body)
        try:
            return await asyncio.wait_for(future, _SEND_TIMEOUT_S)
        except TimeoutError as exc:
            self._pending.pop(echo, None)
            raise RuntimeError("onebot send_msg timed out") from exc

    async def _cleanup(self) -> None:
        self._fail_pending("onebot adapter stopping")
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        runner, self._runner = self._runner, None
        if runner is not None:
            with contextlib.suppress(Exception):
                await runner.cleanup()
        session, self._session = self._session, None
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()

    def _fail_pending(self, message: str) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))


def _authorized(request: web.Request, token: str) -> bool:
    if not token:
        return True
    header = request.headers.get("Authorization", "")
    if header in {f"Bearer {token}", f"Token {token}"}:
        return True
    return request.query.get("access_token") == token


def _as_id(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value
