"""Telegram Bot API adapter: long polling with aiohttp, no official SDK.

Private and group chats are supported. Group messages only reach the pipeline
when they wake the bot (prefix, mention of the bot, or reply to it) — Telegram's
privacy mode hides non-command group messages from bots entirely, which is a
BotFather setting, not something the adapter can change.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

import aiohttp
import orjson
from pydantic import BaseModel, ConfigDict, Field

from verter.core.config import PlatformConfig
from verter.core.log import logger
from verter.core.message.components import At, AtAll, Image, MessageComponent, Plain, Reply
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata


class TelegramError(RuntimeError):
    """The Bot API rejected a request or the adapter could not reach it."""


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
        self._last_sent: dict[str, float] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}

    def meta(self) -> PlatformMetadata:
        return self._meta

    @property
    def bot_username(self) -> str:
        return str(self._me.get("username", ""))

    async def run(self) -> None:
        self._stopping.clear()
        settings = self.settings
        async with aiohttp.ClientSession() as session:
            self._me = _as_dict(await self._api(session, "getMe"))
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
                    logger.warning("Telegram %s: getUpdates failed: %s", self.config.id, exc)
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
    ) -> Any:
        """POST to ``{base}/bot{token}/{action}`` and return the ``result`` field."""
        url = f"{self.settings.api_base.rstrip('/')}/bot{self.settings.token}/{action}"
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.post(
            url,
            data=orjson.dumps(dict(params or {})),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise TelegramError(f"{action}: unauthorized (bad bot token)")
            if response.status != 200:
                raise TelegramError(f"{action}: HTTP {response.status}")
            body: Any = await response.json(loads=orjson.loads)
        payload = _as_dict(body)
        if not payload.get("ok"):
            raise TelegramError(f"{action}: {payload.get('description', 'unknown error')}")
        return payload.get("result")

    # -- sending ------------------------------------------------------------------------

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        lock = self._send_locks.setdefault(session.session_id, asyncio.Lock())
        async with lock:
            await self._send_locked(session, message)

    async def _send_locked(self, session: MessageSession, message: MessageChain) -> None:
        delay = self.settings.send_interval_s - (
            time.monotonic() - self._last_sent.get(session.session_id, 0.0)
        )
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await self._dispatch_chain(session, message)
        finally:
            self._last_sent[session.session_id] = time.monotonic()

    async def _dispatch_chain(self, session: MessageSession, message: MessageChain) -> None:
        async with aiohttp.ClientSession() as http:
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
                elif isinstance(component, AtAll):
                    text_parts.append("@all")
                elif isinstance(component, Reply):
                    reply_to = component.message_id or None
                elif isinstance(component, Image):
                    images.append(component)
            text = "".join(text_parts).strip()
            chat_id = session.session_id
            if text or not images:
                await self._api(
                    http,
                    "sendMessage",
                    _msg_params(chat_id, {"text": text}, reply_to),
                )
            for image in images:
                await self._send_photo(http, chat_id, image, reply_to)

    async def _send_photo(
        self, http: aiohttp.ClientSession, chat_id: str, image: Image, reply_to: str | None
    ) -> None:
        file = image.file
        if file.startswith(("http://", "https://")):
            await self._api(http, "sendPhoto", _msg_params(chat_id, {"photo": file}, reply_to))
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
        form.add_field("photo", data, filename="image.png")
        url = f"{self.settings.api_base.rstrip('/')}/bot{self.settings.token}/sendPhoto"
        async with http.post(url, data=form) as response:
            body: Any = await response.json(loads=orjson.loads)
            payload = _as_dict(body)
            if response.status != 200 or not payload.get("ok"):
                raise TelegramError(
                    f"sendPhoto: HTTP {response.status} {payload.get('description', '')}"
                )


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
