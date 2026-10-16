"""OneBot v11 adapter: reverse WebSocket (server) and forward WebSocket (client).

Targets NapCat's OneBot v11 implementation. Only WebSocket array-format message
segments are supported; HTTP transport is out of scope (see docs/P2_P4_PLAN.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import aiohttp
import orjson
from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field

from verter.core.config import PlatformConfig
from verter.core.log import logger
from verter.core.message.components import (
    At,
    AtAll,
    Contact,
    Dice,
    Face,
    File,
    Forward,
    Image,
    Json,
    MessageComponent,
    Mface,
    Music,
    Node,
    Plain,
    Poke,
    Record,
    Reply,
    Rps,
    Unknown,
    Video,
)
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata

_RECONNECT_MAX_S = 30.0
_API_TIMEOUT_S = 30.0
_REPLY_LOOKUP_TIMEOUT_S = 5.0
_HEARTBEAT_S = 30.0


class OneBotActionError(RuntimeError):
    """NapCat answered an action with ``status: failed`` or a non-zero ``retcode``."""

    def __init__(self, action: str, retcode: object, message: str) -> None:
        super().__init__(f"{action} failed (retcode={retcode}): {message or 'no message'}")
        self.action = action
        self.retcode = retcode


class OneBotSettings(BaseModel):
    """Adapter-specific keys under a ``type: onebot_v11`` platform entry."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ws_reverse", "ws_forward"] = "ws_reverse"
    access_token: str = ""
    """Shared secret. Reverse mode checks the inbound ``Authorization`` header;
    forward mode sends it when connecting out."""

    # ws_reverse: verter runs the WebSocket server; NapCat connects in.
    # Port 0 is allowed so tests can ask the OS for an ephemeral port.
    ws_reverse_host: str = "127.0.0.1"
    ws_reverse_port: int = Field(default=6199, ge=0, le=65535)
    ws_reverse_path: str = "/"

    # ws_forward: verter connects out to NapCat's WebSocket server.
    ws_forward_url: str = "ws://127.0.0.1:3001"
    reconnect_interval_s: float = Field(default=5.0, gt=0)
    """First reconnect delay in forward mode; doubles after each failure, up to 30 s."""


def _str_field(data: dict[str, Any], *keys: str) -> str:
    """First present key in ``data``, coerced to ``str``; empty string if none match."""
    for key in keys:
        value: Any = data.get(key)
        if value:
            return str(value)
    return ""


def _as_dict(data: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}


def _as_dict_list(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [
        cast("dict[str, Any]", item) for item in cast("list[Any]", data) if isinstance(item, dict)
    ]


def _parse_segments(raw: list[dict[str, Any]]) -> list[MessageComponent]:
    """Convert OneBot v11 array-format segments to :class:`MessageComponent`."""
    out: list[MessageComponent] = []
    for segment in raw:
        kind = segment.get("type")
        data_raw: Any = segment.get("data")
        data: dict[str, Any] = (
            cast("dict[str, Any]", data_raw) if isinstance(data_raw, dict) else {}
        )
        if kind == "text":
            out.append(Plain(_str_field(data, "text")))
        elif kind == "at":
            qq = _str_field(data, "qq")
            out.append(AtAll() if qq == "all" else At(qq))
        elif kind == "image":
            out.append(Image(_str_field(data, "url", "file")))
        elif kind == "record":
            out.append(Record(_str_field(data, "url", "file")))
        elif kind == "face":
            out.append(Face(_str_field(data, "id")))
        elif kind == "reply":
            out.append(Reply(_str_field(data, "id")))
        elif kind == "video":
            out.append(Video(_str_field(data, "url", "file")))
        elif kind == "file":
            out.append(File(_str_field(data, "name"), _str_field(data, "url", "file")))
        elif kind == "json":
            out.append(Json(_str_field(data, "data")))
        elif kind == "forward":
            out.append(Forward(_str_field(data, "id")))
        elif kind == "music":
            out.append(
                Music(
                    _str_field(data, "type"),
                    id=_str_field(data, "id"),
                    url=_str_field(data, "url"),
                    audio=_str_field(data, "audio"),
                    title=_str_field(data, "title"),
                    image=_str_field(data, "image"),
                    singer=_str_field(data, "singer"),
                )
            )
        elif kind == "contact":
            out.append(Contact(_str_field(data, "type"), _str_field(data, "id")))
        elif kind == "poke":
            out.append(Poke(_str_field(data, "qq", "id")))
        elif kind == "dice":
            out.append(Dice(_str_field(data, "result")))
        elif kind == "rps":
            out.append(Rps(_str_field(data, "result")))
        elif kind == "mface":
            out.append(
                Mface(
                    _str_field(data, "emoji_id"),
                    _str_field(data, "emoji_package_id"),
                    _str_field(data, "key"),
                    _str_field(data, "summary"),
                )
            )
        else:
            out.append(Unknown(str(kind or "unknown"), data))
    return out


def _render_segments(chain: MessageChain) -> list[dict[str, Any]]:
    """Convert outgoing :class:`MessageComponent`s to OneBot v11 array format."""
    segments: list[dict[str, Any]] = []
    for component in chain:
        if isinstance(component, Plain):
            segments.append({"type": "text", "data": {"text": component.text}})
        elif isinstance(component, At):
            segments.append({"type": "at", "data": {"qq": component.target}})
        elif isinstance(component, AtAll):
            segments.append({"type": "at", "data": {"qq": "all"}})
        elif isinstance(component, Image):
            segments.append({"type": "image", "data": {"file": component.file}})
        elif isinstance(component, Record):
            segments.append({"type": "record", "data": {"file": component.file}})
        elif isinstance(component, Face):
            segments.append({"type": "face", "data": {"id": component.id}})
        elif isinstance(component, Reply):
            segments.append({"type": "reply", "data": {"id": component.message_id}})
        elif isinstance(component, Video):
            segments.append({"type": "video", "data": {"file": component.file}})
        elif isinstance(component, File):
            segments.append(
                {"type": "file", "data": {"file": component.file, "name": component.name}}
            )
        elif isinstance(component, Json):
            segments.append({"type": "json", "data": {"data": component.data}})
        elif isinstance(component, Forward):
            segments.append({"type": "forward", "data": {"id": component.id}})
        elif isinstance(component, Node):
            node: dict[str, Any] = {}
            if component.message_id:
                node["id"] = component.message_id
            if component.content:
                node["content"] = _render_segments(MessageChain(component.content))
            if component.user_id:
                node["user_id"] = component.user_id
            if component.nickname:
                node["nickname"] = component.nickname
            segments.append({"type": "node", "data": node})
        elif isinstance(component, Music):
            music: dict[str, Any] = {"type": component.kind}
            for key in ("id", "url", "audio", "title", "image", "singer"):
                if value := getattr(component, key):
                    music[key] = value
            segments.append({"type": "music", "data": music})
        elif isinstance(component, Contact):
            segments.append(
                {"type": "contact", "data": {"type": component.kind, "id": component.id}}
            )
        elif isinstance(component, Poke):
            poke = {"qq": component.target} if component.target else {}
            segments.append({"type": "poke", "data": poke})
        elif isinstance(component, Dice):
            segments.append({"type": "dice", "data": {"result": component.result}})
        elif isinstance(component, Rps):
            segments.append({"type": "rps", "data": {"result": component.result}})
        elif isinstance(component, Mface):
            segments.append(
                {
                    "type": "mface",
                    "data": {
                        "emoji_id": component.emoji_id,
                        "emoji_package_id": component.emoji_package_id,
                        "key": component.key,
                        "summary": component.summary,
                    },
                }
            )
        elif isinstance(component, Unknown):
            segments.append({"type": component.kind, "data": dict(component.data)})
    return segments


class OneBotEvent(MessageEvent):
    __slots__ = ("_platform", "_session")

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
        self._session = MessageSession(platform_meta.id, message_obj.type, session_id)

    async def _send(self, message: MessageChain) -> None:
        await self._platform.send_by_session(self._session, message)


class _PendingCalls:
    """Matches API responses to requests by their ``echo`` id, per connection."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def new_echo(self) -> tuple[str, asyncio.Future[dict[str, Any]]]:
        echo = str(next(self._counter))
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        return echo, future

    def resolve(self, echo: str | None, payload: dict[str, Any]) -> bool:
        if echo is None:
            return False
        echo = str(echo)
        future = self._pending.pop(echo, None)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    def forget(self, echo: str) -> None:
        self._pending.pop(echo, None)

    def fail_all(self, error: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)


@dataclass(eq=False, slots=True)
class _Connection:
    """One WebSocket to NapCat and the API calls waiting on it."""

    ws: aiohttp.ClientWebSocketResponse | web.WebSocketResponse
    calls: _PendingCalls = field(default_factory=_PendingCalls)

    def __hash__(self) -> int:
        return id(self)

    async def close(self) -> None:
        with contextlib.suppress(ConnectionError):
            await self.ws.close()
        self.calls.fail_all(ConnectionError("OneBot connection closed"))


class OneBotV11Platform(Platform):
    """OneBot v11 adapter. ``mode`` selects reverse (server) or forward (client) WebSocket."""

    def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
        super().__init__(config, context)
        self.settings = self.parse_settings(OneBotSettings)
        self._meta = PlatformMetadata("onebot_v11", config.id, "OneBot v11 (NapCat)")
        self._connections: set[_Connection] = set()
        self._current: _Connection | None = None
        self._events: set[asyncio.Task[None]] = set()
        self._account_online: bool | None = None
        self._last_heartbeat: float | None = None
        self._self_id = ""
        self._stopping = asyncio.Event()
        self._bound_port: int | None = None

    @property
    def connected(self) -> bool:
        """Whether a NapCat connection is currently open."""
        return self._current is not None

    @property
    def online(self) -> bool:
        """Whether NapCat is connected and its QQ account is reported online.

        Uses the latest heartbeat or lifecycle meta event. Before NapCat reports
        either, an open connection counts as online.
        """
        return self._current is not None and self._account_online is not False

    @property
    def last_heartbeat(self) -> float | None:
        """Event-loop time of the last heartbeat, or None if none arrived yet."""
        return self._last_heartbeat

    @property
    def bound_port(self) -> int | None:
        """Actual listening port in reverse mode once the server has started.

        Useful for tests that bind ``ws_reverse_port=0`` and need the OS-assigned port.
        """
        return self._bound_port

    def meta(self) -> PlatformMetadata:
        return self._meta

    async def run(self) -> None:
        self._stopping.clear()
        if self.settings.mode == "ws_reverse":
            await self._run_reverse()
        else:
            await self._run_forward()

    async def terminate(self) -> None:
        self._stopping.set()
        connections = tuple(self._connections)
        for connection in connections:
            await connection.close()
        events = tuple(self._events)
        for task in events:
            task.cancel()
        if events:
            await asyncio.gather(*events, return_exceptions=True)

    # -- reverse mode: verter is the WebSocket server --------------------------------

    async def _run_reverse(self) -> None:
        app = web.Application()
        app.router.add_get(self.settings.ws_reverse_path, self._handle_reverse_connection)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.ws_reverse_host, self.settings.ws_reverse_port)
        try:
            await site.start()
            port = self.settings.ws_reverse_port
            if port == 0:
                sockets = runner.addresses
                if sockets:
                    port = sockets[0][1]
            self._bound_port = port
            logger.info(
                "OneBot %s listening on ws://%s:%d%s",
                self.config.id,
                self.settings.ws_reverse_host,
                port,
                self.settings.ws_reverse_path,
            )
            await self._stopping.wait()
        finally:
            self._bound_port = None
            await runner.cleanup()

    async def _handle_reverse_connection(self, request: web.Request) -> web.WebSocketResponse:
        token = self.settings.access_token
        if token:
            header = request.headers.get("Authorization", "")
            if header not in (f"Bearer {token}", token):
                raise web.HTTPUnauthorized(text="invalid access_token")
        ws = web.WebSocketResponse(heartbeat=_HEARTBEAT_S)
        await ws.prepare(request)
        connection = _Connection(ws)
        self._connections.add(connection)
        self._current = connection
        logger.info("OneBot %s: peer connected (%d open)", self.config.id, len(self._connections))
        try:
            async for raw in ws:
                if raw.type is aiohttp.WSMsgType.TEXT:
                    await self._dispatch(connection, raw.data)
                elif raw.type is aiohttp.WSMsgType.ERROR:
                    break
        finally:
            self._connections.discard(connection)
            if self._current is connection:
                self._current = None
            connection.calls.fail_all(ConnectionError("OneBot connection closed"))
        return ws

    # -- forward mode: verter is the WebSocket client ---------------------------------

    async def _run_forward(self) -> None:
        delay = self.settings.reconnect_interval_s
        headers: dict[str, str] = {}
        if self.settings.access_token:
            headers["Authorization"] = f"Bearer {self.settings.access_token}"
        async with aiohttp.ClientSession() as session:
            while not self._stopping.is_set():
                connection: _Connection | None = None
                try:
                    async with session.ws_connect(
                        self.settings.ws_forward_url,
                        headers=headers,
                        heartbeat=_HEARTBEAT_S,
                    ) as ws:
                        connection = _Connection(ws)
                        self._connections.add(connection)
                        self._current = connection
                        logger.info(
                            "OneBot %s connected to %s",
                            self.config.id,
                            self.settings.ws_forward_url,
                        )
                        delay = self.settings.reconnect_interval_s
                        async for raw in ws:
                            if raw.type is aiohttp.WSMsgType.TEXT:
                                await self._dispatch(connection, raw.data)
                            elif raw.type is aiohttp.WSMsgType.ERROR:
                                break
                except asyncio.CancelledError:
                    if not self._stopping.is_set():
                        raise
                except (aiohttp.ClientError, ConnectionError, OSError) as exc:
                    logger.warning("OneBot %s connection failed: %s", self.config.id, exc)
                finally:
                    if connection is not None:
                        self._connections.discard(connection)
                        if self._current is connection:
                            self._current = None
                        connection.calls.fail_all(ConnectionError("OneBot connection closed"))
                if self._stopping.is_set():
                    return
                logger.info("OneBot %s reconnecting in %.1fs", self.config.id, delay)
                try:
                    await asyncio.wait_for(self._stopping.wait(), delay)
                except TimeoutError:
                    delay = min(delay * 2, _RECONNECT_MAX_S)

    # -- shared: event and API-response dispatch ---------------------------------------

    async def _dispatch(self, connection: _Connection, raw_text: str) -> None:
        try:
            payload: dict[str, Any] = orjson.loads(raw_text)
        except orjson.JSONDecodeError:
            logger.warning("OneBot %s: invalid JSON payload", self.config.id)
            return
        if ("echo" in payload or "retcode" in payload) and connection.calls.resolve(
            payload.get("echo"), payload
        ):
            return
        post_type = payload.get("post_type")
        if post_type == "message":
            # Handled in its own task: it may call back into NapCat (e.g. get_msg),
            # which only resolves if this read loop keeps running.
            task = asyncio.ensure_future(self._handle_message_event(payload))
            self._events.add(task)
            task.add_done_callback(self._events.discard)
        elif post_type == "meta_event":
            self._handle_meta_event(payload)
        elif post_type in ("notice", "request"):
            self._handle_notice_or_request(payload)

    def _handle_meta_event(self, payload: dict[str, Any]) -> None:
        self_id = payload.get("self_id")
        if self_id is not None:
            self._self_id = str(self_id)
        meta_type = payload.get("meta_event_type")
        if meta_type == "heartbeat":
            self._last_heartbeat = asyncio.get_running_loop().time()
            status: Any = payload.get("status")
            if isinstance(status, dict):
                online: Any = cast("dict[str, Any]", status).get("online")
                if isinstance(online, bool):
                    self._account_online = online
        elif meta_type == "lifecycle":
            sub_type = payload.get("sub_type")
            if sub_type in ("enable", "connect"):
                self._account_online = True
            elif sub_type == "disable":
                self._account_online = False

    def _handle_notice_or_request(self, payload: dict[str, Any]) -> None:
        """Turn a notice or request event into a pipeline event plugins can subscribe to.

        The event carries no message segments. Its outline is ``notice_type.sub_type``
        (or ``request_type.sub_type``), and the raw payload is on ``message_obj.raw_message``.
        """
        post_type = str(payload.get("post_type"))
        kind = MessageType.NOTICE if post_type == "notice" else MessageType.REQUEST
        detail = str(payload.get("notice_type") or payload.get("request_type") or "")
        sub = _str_field(payload, "sub_type")
        outline = f"{detail}.{sub}" if sub else detail

        group_id = _str_field(payload, "group_id")
        user_id = _str_field(payload, "user_id")
        self_id = _str_field(payload, "self_id") or self._self_id
        session_id = group_id or user_id or self_id

        bot_message = BotMessage(
            type=kind,
            self_id=self_id,
            session_id=session_id,
            message_id=_str_field(payload, "message_id"),
            sender=MessageMember(user_id),
            message=[],
            message_str=outline,
            group=Group(group_id) if group_id else None,
            raw_message=payload,
        )
        self.commit_event(OneBotEvent(outline, bot_message, self._meta, session_id, self))

    async def _handle_message_event(self, payload: dict[str, Any]) -> None:
        self_id = str(payload.get("self_id", self._self_id))
        self._self_id = self_id or self._self_id
        message_type = payload.get("message_type")
        raw_segments: Any = payload.get("message")
        if not isinstance(raw_segments, list):
            logger.warning(
                "OneBot %s: expected array message format, got %r; "
                "set messagePostFormat to 'array' in the NapCat config",
                self.config.id,
                type(raw_segments).__name__,
            )
            return
        segments = _parse_segments(cast("list[dict[str, Any]]", raw_segments))
        await self._fill_reply_senders(segments)
        message_str = "".join(c.outline() for c in segments if isinstance(c, Plain))
        sender_raw: Any = payload.get("sender")
        sender: dict[str, Any] = (
            cast("dict[str, Any]", sender_raw) if isinstance(sender_raw, dict) else {}
        )
        sender_id = _str_field(payload, "user_id") or _str_field(sender, "user_id")
        sender_name = _str_field(sender, "card", "nickname")

        if message_type == "group":
            group_id = str(payload.get("group_id", ""))
            kind = MessageType.GROUP_MESSAGE
            session_id = group_id
            group = Group(group_id)
        else:
            kind = MessageType.FRIEND_MESSAGE
            session_id = sender_id
            group = None

        bot_message = BotMessage(
            type=kind,
            self_id=self_id,
            session_id=session_id,
            message_id=str(payload.get("message_id", "")),
            sender=MessageMember(sender_id, sender_name),
            message=segments,
            message_str=message_str,
            group=group,
            raw_message=payload,
        )
        event = OneBotEvent(message_str, bot_message, self._meta, session_id, self)
        self.commit_event(event)

    # -- outgoing API calls -------------------------------------------------------------

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        action = (
            "send_group_msg"
            if session.message_type is MessageType.GROUP_MESSAGE
            else "send_private_msg"
        )
        key = "group_id" if session.message_type is MessageType.GROUP_MESSAGE else "user_id"
        await self.call(action, {key: session.session_id, "message": _render_segments(message)})

    async def call(
        self,
        action: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = _API_TIMEOUT_S,
    ) -> Any:
        """Call a NapCat action over the newest open connection and return its ``data``.

        ``data`` is returned as NapCat sent it: a dict for most actions, a list for
        list actions such as ``get_group_member_list``, or None.

        Raises:
            ConnectionError: If no connection is open or it closes before answering.
            TimeoutError: If NapCat does not answer within 30 seconds.
            OneBotActionError: If NapCat reports the action as failed.
        """
        connection = self._current
        if connection is None:
            raise ConnectionError(f"OneBot {self.config.id} has no active connection")
        echo, future = connection.calls.new_echo()
        payload = orjson.dumps({"action": action, "params": dict(params or {}), "echo": echo})
        await connection.ws.send_str(payload.decode())
        try:
            reply = await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            connection.calls.forget(echo)
            raise
        if reply.get("status") != "ok" or reply.get("retcode") not in (0, None):
            raise OneBotActionError(action, reply.get("retcode"), str(reply.get("message", "")))
        return reply.get("data")

    # -- typed wrappers for common actions; any other action goes through call() --------

    async def get_login_info(self) -> dict[str, Any]:
        return _as_dict(await self.call("get_login_info"))

    async def get_msg(self, message_id: str) -> dict[str, Any]:
        return _as_dict(await self.call("get_msg", {"message_id": message_id}))

    async def delete_msg(self, message_id: str) -> None:
        """Recall a message."""
        await self.call("delete_msg", {"message_id": message_id})

    async def set_group_ban(self, group_id: str, user_id: str, duration_s: int = 1800) -> None:
        """Mute a member for ``duration_s`` seconds; 0 lifts the mute."""
        await self.call(
            "set_group_ban", {"group_id": group_id, "user_id": user_id, "duration": duration_s}
        )

    async def set_group_whole_ban(self, group_id: str, *, enable: bool = True) -> None:
        await self.call("set_group_whole_ban", {"group_id": group_id, "enable": enable})

    async def set_group_kick(
        self, group_id: str, user_id: str, *, reject_add_request: bool = False
    ) -> None:
        await self.call(
            "set_group_kick",
            {"group_id": group_id, "user_id": user_id, "reject_add_request": reject_add_request},
        )

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        return _as_dict(await self.call("get_group_info", {"group_id": group_id}))

    async def get_group_member_info(self, group_id: str, user_id: str) -> dict[str, Any]:
        return _as_dict(
            await self.call("get_group_member_info", {"group_id": group_id, "user_id": user_id})
        )

    async def get_group_member_list(self, group_id: str) -> list[dict[str, Any]]:
        return _as_dict_list(await self.call("get_group_member_list", {"group_id": group_id}))

    async def get_group_list(self) -> list[dict[str, Any]]:
        return _as_dict_list(await self.call("get_group_list"))

    async def get_friend_list(self) -> list[dict[str, Any]]:
        return _as_dict_list(await self.call("get_friend_list"))

    async def set_friend_add_request(
        self, flag: str, *, approve: bool = True, remark: str = ""
    ) -> None:
        await self.call(
            "set_friend_add_request", {"flag": flag, "approve": approve, "remark": remark}
        )

    async def set_group_add_request(
        self, flag: str, sub_type: str, *, approve: bool = True, reason: str = ""
    ) -> None:
        """Answer a join (``sub_type='add'``) or invite (``sub_type='invite'``) request."""
        await self.call(
            "set_group_add_request",
            {"flag": flag, "sub_type": sub_type, "approve": approve, "reason": reason},
        )

    async def _fill_reply_senders(self, segments: list[MessageComponent]) -> None:
        """Resolve who sent each quoted message, so quoting the bot can wake it."""
        for segment in segments:
            if not isinstance(segment, Reply) or not segment.message_id or segment.sender_id:
                continue
            try:
                data = _as_dict(
                    await self.call(
                        "get_msg",
                        {"message_id": segment.message_id},
                        timeout_s=_REPLY_LOOKUP_TIMEOUT_S,
                    )
                )
            except (ConnectionError, TimeoutError, OneBotActionError) as exc:
                logger.debug("get_msg(%s) failed: %s", segment.message_id, exc)
                continue
            sender: Any = data.get("sender")
            if isinstance(sender, dict):
                segment.sender_id = _str_field(cast("dict[str, Any]", sender), "user_id")
