"""Telegram Bot API adapter: long polling with aiohttp, no official SDK.

Private and group chats are supported. Group messages only reach the pipeline
when they wake the bot (prefix, mention of the bot, or reply to it) — Telegram's
privacy mode hides non-command group messages from bots entirely, which is a
BotFather setting, not something the adapter can change.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

import aiohttp
import orjson
from pydantic import BaseModel, ConfigDict, Field

from verter.core.config import PlatformConfig
from verter.core.log import logger
from verter.core.message.components import At, Image, MessageComponent, Plain, Reply
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata


class TelegramError(RuntimeError):
    """The Bot API rejected a request or the adapter could not reach it."""


_MAX_TEXT = 4096
_MAX_RETRIES = 3
_RETRY_BASE_S = 1.0
_RETRY_CAP_S = 30.0
_GLOBAL_RATE = 30.0
"""Global send ceiling, matching Telegram's roughly 30 messages per second."""
_GROUP_WINDOW_S = 60.0
_GROUP_WINDOW_MAX = 18
"""Per-group sends kept under Telegram's roughly 20 per minute."""
_LOCK_IDLE_S = 600.0
_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+")


class TelegramSettings(BaseModel):
    """Adapter-specific keys under a ``type: telegram`` platform entry."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    """Bot token from BotFather. Supports ``ENV:NAME`` to read the environment."""
    api_base: str = "https://api.telegram.org"
    poll_timeout_s: int = Field(default=25, ge=1, le=60)
    """Long-poll duration. A blocked poll is cut short by task cancellation on stop."""
    send_interval_s: float = Field(default=1.1, gt=0)
    """Minimum interval between sends to one chat; Telegram allows about 1 per second."""
    retry_interval_s: float = Field(default=5.0, gt=0)
    """Delay before re-polling after a failed ``getUpdates``."""


def _utf16_map(text: str) -> dict[int, int]:
    """Map UTF-16 code-unit offsets to Python code-point indices."""
    mapping: dict[int, int] = {}
    units = 0
    for index, char in enumerate(text):
        mapping[units] = index
        units += 2 if ord(char) > 0xFFFF else 1
    mapping[units] = len(text)
    return mapping


def _strip_command_mention(components: list[MessageComponent], bot_username: str) -> bool:
    """Remove an ``@botname`` suffix from a leading ``/command@bot``.

    Returns False when the command names a different bot, so the caller drops it.
    Operates on already-parsed components, so later entity offsets stay valid.
    """
    first = components[0] if components else None
    if not isinstance(first, Plain) or not first.text.startswith("/"):
        return True
    head, _, rest = first.text.partition(" ")
    name, separator, target = head.partition("@")
    if not separator:
        return True
    if target.lower() != bot_username.lower():
        return False
    components[0] = Plain(name + (" " + rest if rest else ""))
    if components[0].text == "":
        components.pop(0)
    return True


def _parse_text(
    text: str, entities: list[dict[str, Any]], bot_username: str
) -> list[MessageComponent]:
    """Split text into components, turning mentions of the bot into At parts.

    Telegram entity offsets are UTF-16 code units, not code points.
    """
    offsets = _utf16_map(text)
    components: list[MessageComponent] = []
    cursor = 0
    for entity in sorted(entities, key=lambda e: int(e.get("offset", 0))):
        kind = entity.get("type")
        if kind != "mention":
            continue
        start = offsets.get(int(entity.get("offset", -1)))
        end = offsets.get(int(entity.get("offset", -1)) + int(entity.get("length", 0)))
        if start is None or end is None or start < cursor or end > len(text):
            continue
        mention = text[start:end]
        if mention.lstrip("@").lower() != bot_username.lower():
            continue
        if start > cursor:
            components.append(Plain(text[cursor:start]))
        components.append(At(bot_username, bot_username))
        cursor = end
    if cursor < len(text):
        components.append(Plain(text[cursor:]))
    return components or ([Plain(text)] if text else [])


class TelegramEvent(MessageEvent):
    __slots__ = ("_platform", "_session")

    def __init__(
        self,
        message_str: str,
        message_obj: BotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        platform: TelegramPlatform,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._platform = platform
        self._session = MessageSession(platform_meta.id, message_obj.type, session_id)

    async def _send(self, message: MessageChain) -> None:
        await self._platform.send_by_session(self._session, message)


class TelegramPlatform(Platform):
    """Telegram adapter. ``run()`` long-polls ``getUpdates`` until terminated."""

    def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
        super().__init__(config, context)
        self.settings = self.parse_settings(TelegramSettings)
        self._meta = PlatformMetadata("telegram", config.id, "Telegram Bot API")
        self._stopping = asyncio.Event()
        self._me: dict[str, Any] = {}
        self._session: aiohttp.ClientSession | None = None
        self._last_sent: dict[str, float] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._group_sent: dict[str, list[float]] = {}
        self._global_next = 0.0
        self._global_lock = asyncio.Lock()
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._clock: Callable[[], float] = time.monotonic

    def meta(self) -> PlatformMetadata:
        return self._meta

    @property
    def bot_username(self) -> str:
        return str(self._me.get("username", ""))

    async def run(self) -> None:
        self._stopping.clear()
        settings = self.settings
        async with aiohttp.ClientSession() as session:
            self._session = session
            try:
                await self._poll(session, settings)
            finally:
                self._session = None

    async def _poll(self, session: aiohttp.ClientSession, settings: TelegramSettings) -> None:
        self._me = _as_dict(
            await self._api(session, "getMe", timeout_s=self.settings.poll_timeout_s)
        )
        if not self.bot_username:
            raise TelegramError("getMe returned no bot username")
        logger.info("Telegram %s: polling as @%s", self.config.id, self.bot_username)
        offset = 0
        while not self._stopping.is_set():
            try:
                updates = await self._api(
                    session,
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": settings.poll_timeout_s,
                        "allowed_updates": ["message"],
                    },
                    timeout_s=settings.poll_timeout_s + 15,
                )
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, OSError, TelegramError) as exc:
                logger.warning("Telegram %s: getUpdates failed: %s", self.config.id, _safe(exc))
                waiter = asyncio.ensure_future(self._stopping.wait())
                await asyncio.wait([waiter], timeout=settings.retry_interval_s)
                waiter.cancel()
                continue
            for update in updates:
                offset = int(update.get("update_id", offset)) + 1
                self._handle_update(update)

    async def terminate(self) -> None:
        self._stopping.set()

    # -- receiving ----------------------------------------------------------------------

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = _as_dict(update.get("message"))
        if not message:
            return
        chat = _as_dict(message.get("chat"))
        chat_type = str(chat.get("type", ""))
        text = str(message.get("text") or message.get("caption") or "")
        if not text:
            return
        sender = _as_dict(message.get("from"))
        sender_id = str(sender.get("id", ""))
        sender_name = str(sender.get("username") or sender.get("first_name") or "")
        self_id = self.bot_username

        components = _parse_text(text, _as_dict_list(message.get("entities")), self_id)
        if not _strip_command_mention(components, self_id):
            # A command addressed to another bot is not ours to handle.
            return
        text = "".join(c.text for c in components if isinstance(c, Plain))
        quoted_raw: Any = message.get("reply_to_message")
        if isinstance(quoted_raw, dict):
            quoted = cast("dict[str, Any]", quoted_raw)
            quoted_sender = _as_dict(quoted.get("from"))
            components.insert(
                0,
                Reply(
                    str(quoted.get("message_id", "")),
                    str(quoted_sender.get("username") or quoted_sender.get("id", "")),
                ),
            )

        if chat_type == "private":
            kind = MessageType.FRIEND_MESSAGE
            session_id = sender_id
            group = None
        else:
            kind = MessageType.GROUP_MESSAGE
            session_id = str(chat.get("id", ""))
            group = Group(session_id, str(chat.get("title", "")))

        bot_message = BotMessage(
            type=kind,
            self_id=self_id,
            session_id=session_id,
            message_id=str(message.get("message_id", "")),
            sender=MessageMember(sender_id, sender_name),
            message=components,
            message_str=text,
            group=group,
            raw_message=message,
        )
        self.commit_event(TelegramEvent(text, bot_message, self._meta, session_id, self))

    # -- Bot API client -----------------------------------------------------------------

    async def _api(
        self,
        session: aiohttp.ClientSession,
        action: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = 60.0,
        retry: bool = True,
    ) -> Any:
        """POST JSON to ``{base}/bot{token}/{action}`` and return ``result``.

        When ``retry`` is set, HTTP 429 and 5xx responses and network errors are
        retried a few times; 400, 401, and 403 fail immediately.
        """
        return await self._request(
            session,
            action,
            data=orjson.dumps(dict(params or {})),
            headers={"Content-Type": "application/json"},
            retry=retry,
            timeout_s=timeout_s,
        )

    async def _request(
        self,
        session: aiohttp.ClientSession,
        action: str,
        *,
        data: Any,
        headers: dict[str, str] | None = None,
        timeout_s: float = 60.0,
        retry: bool = True,
    ) -> Any:
        url = f"{self.settings.api_base.rstrip('/')}/bot{self.settings.token}/{action}"
        attempts = _MAX_RETRIES if retry else 1
        for attempt in range(attempts):
            try:
                status, payload = await self._post(session, url, data, headers, timeout_s)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                if attempt + 1 == attempts:
                    # from None: the chained aiohttp error's URL holds the token.
                    raise TelegramError(f"{action}: {type(exc).__name__}") from None
                await self._sleep(self._backoff(attempt))
                continue
            if payload.get("ok") and status == 200:
                return payload.get("result")
            if status in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                wait = _retry_after(payload) if status == 429 else self._backoff(attempt)
                logger.warning(
                    "Telegram %s: %s HTTP %d, retrying in %.1fs",
                    self.config.id,
                    action,
                    status,
                    wait,
                )
                await self._sleep(wait)
                continue
            raise self._error(action, status, payload)
        raise TelegramError(f"{action}: no response")  # unreachable; keeps the type checker happy

    async def _post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        data: Any,
        headers: dict[str, str] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.post(url, data=data, headers=headers, timeout=timeout) as response:
            try:
                body: Any = await response.json(loads=orjson.loads, content_type=None)
            except orjson.JSONDecodeError:
                body = {}
        return response.status, _as_dict(body)

    def _error(self, action: str, status: int, payload: dict[str, Any]) -> TelegramError:
        description = str(payload.get("description") or "")
        if status == 401:
            return TelegramError(f"{action}: unauthorized (bad bot token)")
        if status == 429:
            return TelegramError(
                f"{action}: rate limited, retry after {_retry_after(payload):.0f}s"
            )
        return TelegramError(f"{action}: HTTP {status} {description}".rstrip())

    def _backoff(self, attempt: int) -> float:
        return min(_RETRY_BASE_S * 2**attempt, _RETRY_CAP_S)

    # -- sending ------------------------------------------------------------------------

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        lock = self._send_locks.setdefault(session.session_id, asyncio.Lock())
        async with lock:
            await self._pace(session)
            await self._dispatch_chain(session, message)
            self._last_sent[session.session_id] = self._clock()
            self._sweep_idle()

    async def _pace(self, session: MessageSession) -> None:
        """Reserve a send slot, then wait for it outside the lock.

        The lock only covers the reservation, so concurrent sends to different
        chats queue instead of all reading the same slot and bursting together.
        """
        async with self._global_lock:
            now = self._clock()
            slot = now
            per_chat = self._last_sent.get(session.session_id, 0.0) + self.settings.send_interval_s
            slot = max(slot, per_chat, self._global_next)
            if session.message_type is MessageType.GROUP_MESSAGE:
                recent = [
                    t
                    for t in self._group_sent.get(session.session_id, [])
                    if slot - t < _GROUP_WINDOW_S
                ]
                if len(recent) >= _GROUP_WINDOW_MAX:
                    slot = max(slot, recent[0] + _GROUP_WINDOW_S)
                self._group_sent[session.session_id] = [
                    t for t in recent if slot - t < _GROUP_WINDOW_S
                ]
                self._group_sent[session.session_id].append(slot)
            self._global_next = slot + 1 / _GLOBAL_RATE
            self._last_sent[session.session_id] = slot
        wait = slot - self._clock()
        if wait > 0:
            await self._sleep(wait)

    def _sweep_idle(self) -> None:
        cutoff = self._clock() - _LOCK_IDLE_S
        stale = [
            chat
            for chat, sent in self._last_sent.items()
            if sent < cutoff and not self._send_locks[chat].locked()
        ]
        for chat in stale:
            self._last_sent.pop(chat, None)
            self._send_locks.pop(chat, None)
            self._group_sent.pop(chat, None)

    async def _http(self) -> tuple[aiohttp.ClientSession, bool]:
        """The polling session while ``run()`` is active, otherwise a fresh one."""
        if self._session is not None and not self._session.closed:
            return self._session, False
        return aiohttp.ClientSession(), True

    async def _dispatch_chain(self, session: MessageSession, message: MessageChain) -> None:
        http, owned = await self._http()
        try:
            await self._send_chain(http, session, message)
        finally:
            if owned:
                await http.close()

    async def _send_chain(
        self, http: aiohttp.ClientSession, session: MessageSession, message: MessageChain
    ) -> None:
        reply_to: str | None = None
        text_parts: list[str] = []
        images: list[Image] = []
        for component in message:
            if isinstance(component, Plain):
                text_parts.append(component.text)
            elif isinstance(component, At):
                mention = f"@{component.target.lstrip('@')}"
                text_parts.append(
                    mention if not text_parts or text_parts[-1].endswith(" ") else f" {mention}"
                )
            elif isinstance(component, Reply):
                reply_to = component.message_id or None
            elif isinstance(component, Image):
                images.append(component)
            # AtAll is dropped: Telegram has no @everyone equivalent.
        text = "".join(text_parts).strip()
        chat_id = session.session_id
        if images and text and _utf16_len(text) <= 1024:
            await self._send_photo(http, chat_id, images[0], reply_to, caption=text)
            images = images[1:]
            text = ""
        for index, chunk in enumerate(_chunks(text)):
            await self._api(
                http,
                "sendMessage",
                _msg_params(chat_id, {"text": chunk}, reply_to if index == 0 else None),
            )
        for image in images:
            await self._send_photo(http, chat_id, image, reply_to)

    async def _send_photo(
        self,
        http: aiohttp.ClientSession,
        chat_id: str,
        image: Image,
        reply_to: str | None,
        *,
        caption: str = "",
    ) -> None:
        file = image.file
        if file.startswith(("http://", "https://")):
            params = {"photo": file}
            if caption:
                params["caption"] = caption
            await self._api(http, "sendPhoto", _msg_params(chat_id, params, reply_to))
            return
        if file.startswith("base64://"):
            data = base64.b64decode(file[len("base64://") :])
        elif file.startswith("file://"):
            data = await asyncio.to_thread(_local_path(file).read_bytes)
        else:
            raise TelegramError(f"unsupported image reference: {file[:60]}")
        form = aiohttp.FormData()
        form.add_field("chat_id", chat_id)
        if reply_to:
            form.add_field("reply_to_message_id", reply_to)
        if caption:
            form.add_field("caption", caption)
        form.add_field("photo", data, filename="image.png")
        await self._request(http, "sendPhoto", data=form)


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units, which is how Telegram counts text."""
    return len(text.encode("utf-16-le")) // 2


def _chunks(text: str, limit: int = _MAX_TEXT) -> list[str]:
    """Split ``text`` into pieces within ``limit`` UTF-16 code units.

    Prefers to break at the last newline or space, and returns nothing for empty text.
    ``window`` is the longest prefix that fits the limit, so its code-point indices
    are valid cut points for slicing.
    """
    pieces: list[str] = []
    rest = text.strip()
    while _utf16_len(rest) > limit:
        window = rest.encode("utf-16-le")[: limit * 2].decode("utf-16-le", errors="ignore")
        cut = len(window)
        for sep in ("\n", " "):
            at = window.rfind(sep)
            if at > 0:
                cut = at
                break
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def _retry_after(payload: dict[str, Any]) -> float:
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        value = cast("dict[str, Any]", parameters).get("retry_after")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return _RETRY_BASE_S


def _safe(exc: BaseException) -> str:
    """Exception text with any bot token removed, so logs cannot leak it."""
    return _TOKEN_IN_URL.sub("/bot<redacted>", str(exc))


def _local_path(file_uri: str) -> Path:
    """Turn a ``file://`` URI into a local path, including Windows drive letters."""
    parsed = urlparse(file_uri)
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


def _msg_params(chat_id: str, extra: dict[str, Any], reply_to: str | None) -> dict[str, Any]:
    params = {"chat_id": chat_id, **extra}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    return params


def _as_dict(data: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}


def _as_dict_list(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [
        cast("dict[str, Any]", item) for item in cast("list[Any]", data) if isinstance(item, dict)
    ]
