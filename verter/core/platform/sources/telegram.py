"""Telegram Bot API adapter using long polling. Webhooks are not implemented."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping, Sequence
from typing import cast
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import BaseModel, ConfigDict, Field

from verter.core.config import ConfigError, PlatformConfig
from verter.core.log import logger
from verter.core.message.components import At, Image, MessageComponent, Plain, Reply
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.bot_message import BotMessage, Group, MessageMember
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform import Platform, PlatformContext
from verter.core.platform.platform_metadata import PlatformMetadata
from verter.core.platform.sources.onebot_v11 import resolve_secret

_PRIVATE_GAP_S = 1.05
_GROUP_GAP_S = 3.05
_RETRY_INITIAL_S = 1.0
_RETRY_MAX_S = 30.0


class TelegramSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    api_base: str = "https://api.telegram.org"
    poll_timeout_s: int = Field(default=25, ge=1, le=50)
    throttle_s: float = Field(default=-1, ge=-1, le=60)
    """Send spacing. ``-1`` uses Telegram FAQ defaults; ``0`` disables it (tests)."""


class TelegramEvent(MessageEvent):
    __slots__ = ("_platform",)

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

    async def _send(self, message: MessageChain) -> None:
        await self._platform.send_chain(self.message_obj, message)


class TelegramPlatform(Platform):
    def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
        super().__init__(config, context)
        self.settings = self.parse_settings(TelegramSettings)
        self._token = resolve_secret(self.settings.token)
        if not self._token:
            raise ConfigError("telegram token is empty")
        self._meta = PlatformMetadata("telegram", config.id, "Telegram Bot API (long polling)")
        self._closed = asyncio.Event()
        self._session: ClientSession | None = None
        self._self_id = ""
        self._offset = 0
        self._last_sent: dict[str, float] = {}

    def meta(self) -> PlatformMetadata:
        return self._meta

    async def run(self) -> None:
        timeout = ClientTimeout(total=self.settings.poll_timeout_s + 10)
        self._session = ClientSession(timeout=timeout)
        try:
            me = _as_mapping(await self._api("getMe"))
            self._self_id = str(me.get("id", ""))
            delay = _RETRY_INITIAL_S
            while not self._closed.is_set():
                try:
                    updates = await self._get_updates()
                    delay = _RETRY_INITIAL_S
                except (ClientError, TimeoutError, RuntimeError) as exc:
                    if self._closed.is_set():
                        return
                    logger.warning("Telegram getUpdates failed: %s; retry in %.1fs", exc, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, _RETRY_MAX_S)
                    continue
                for update in updates:
                    event = self.event_from_update(update)
                    if event is not None:
                        self.commit_event(event)
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
        chat_id = incoming.group_id or incoming.sender.user_id
        await self._throttle(chat_id, incoming.type is MessageType.GROUP_MESSAGE)
        text, image, reply_to = _outgoing(message.chain)
        payload: dict[str, object] = {"chat_id": _as_id(chat_id)}
        if reply_to:
            payload["reply_to_message_id"] = _as_id(reply_to)
        if image:
            payload["photo"] = image
            if text:
                payload["caption"] = text
            await self._api("sendPhoto", payload)
            return
        payload["text"] = text or " "
        await self._api("sendMessage", payload)

    def event_from_update(self, update: Mapping[str, object]) -> TelegramEvent | None:
        raw = update.get("message") or update.get("edited_message")
        if not isinstance(raw, dict):
            return None
        message = cast("dict[str, object]", raw)
        from_obj = message.get("from")
        from_map = cast("dict[str, object]", from_obj) if isinstance(from_obj, dict) else {}
        chat_obj = message.get("chat")
        chat = cast("dict[str, object]", chat_obj) if isinstance(chat_obj, dict) else {}
        chat_type = str(chat.get("type", "private"))
        user_id = str(from_map.get("id", ""))
        chat_id = str(chat.get("id", user_id))
        kind = MessageType.FRIEND_MESSAGE if chat_type == "private" else MessageType.GROUP_MESSAGE
        session_id = user_id if kind is MessageType.FRIEND_MESSAGE else chat_id
        components = segments_from_telegram(message)
        message_str = "".join(c.text for c in components if isinstance(c, Plain))
        bot_message = BotMessage(
            type=kind,
            self_id=self._self_id,
            session_id=session_id,
            message_id=str(message.get("message_id", "")),
            sender=MessageMember(
                user_id, str(from_map.get("first_name") or from_map.get("username") or "")
            ),
            message=components,
            message_str=message_str,
            group=(
                Group(chat_id, str(chat.get("title", "")))
                if kind is MessageType.GROUP_MESSAGE
                else None
            ),
            raw_message=dict(update),
        )
        return TelegramEvent(message_str, bot_message, self._meta, session_id, self)

    async def _get_updates(self) -> list[dict[str, object]]:
        result = await self._api(
            "getUpdates",
            {
                "offset": self._offset,
                "timeout": self.settings.poll_timeout_s,
                "allowed_updates": ["message"],
            },
        )
        if not isinstance(result, list):
            return []
        updates = [
            cast("dict[str, object]", item)
            for item in cast("list[object]", result)
            if isinstance(item, dict)
        ]
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = max(self._offset, update_id + 1)
        return updates

    async def _api(self, method: str, payload: Mapping[str, object] | None = None) -> object:
        session = self._session
        if session is None:
            raise RuntimeError("telegram session is not running")
        url = f"{self.settings.api_base.rstrip('/')}/bot{quote(self._token, safe=':-_')}/{method}"
        async with session.post(url, json=dict(payload or {})) as response:
            body_obj: object = await response.json(content_type=None)
        if not isinstance(body_obj, dict):
            raise RuntimeError(f"telegram {method} returned a non-object")
        body = cast("dict[str, object]", body_obj)
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {body.get('description', body)}")
        return body.get("result")

    async def _throttle(self, chat_id: str, is_group: bool) -> None:
        if self.settings.throttle_s == 0:
            return
        gap = (
            self.settings.throttle_s
            if self.settings.throttle_s > 0
            else (_GROUP_GAP_S if is_group else _PRIVATE_GAP_S)
        )
        last = self._last_sent.get(chat_id, 0.0)
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_sent[chat_id] = time.monotonic()

    async def _cleanup(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()


def segments_from_telegram(message: Mapping[str, object]) -> list[MessageComponent]:
    """Map a Telegram ``message`` object to Verter segments."""
    parts: list[MessageComponent] = []
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        reply_id = cast("dict[str, object]", reply).get("message_id")
        if reply_id is not None:
            parts.append(Reply(str(reply_id)))
    text = str(message.get("text") or message.get("caption") or "")
    raw_entities = message.get("entities") or message.get("caption_entities")
    entities = cast("list[object]", raw_entities) if isinstance(raw_entities, list) else []
    mentions = _mentions(text, entities)
    if mentions:
        parts.extend(mentions)
    if text:
        parts.append(Plain(text))
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        largest = cast("list[object]", photo)[-1]
        if isinstance(largest, dict):
            file_id = cast("dict[str, object]", largest).get("file_id")
            if file_id is not None:
                parts.append(Image(str(file_id)))
    return parts


def _mentions(text: str, entities: Sequence[object]) -> list[At]:
    found: list[At] = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        entity = cast("dict[str, object]", item)
        kind = str(entity.get("type", ""))
        if kind == "text_mention":
            user = entity.get("user")
            user_map = cast("dict[str, object]", user) if isinstance(user, dict) else {}
            found.append(At(str(user_map.get("id", "")), str(user_map.get("first_name", ""))))
        elif kind == "mention":
            offset = _as_int(entity.get("offset"))
            length = _as_int(entity.get("length"))
            mention = text[offset : offset + length]
            found.append(At(mention.lstrip("@"), mention))
    return found


def _outgoing(chain: Sequence[MessageComponent]) -> tuple[str, str | None, str | None]:
    texts: list[str] = []
    image: str | None = None
    reply_to: str | None = None
    for part in chain:
        if isinstance(part, Plain):
            texts.append(part.text)
        elif isinstance(part, At):
            texts.append(f"@{part.name or part.target}")
        elif isinstance(part, Image) and image is None:
            image = part.file
        elif isinstance(part, Reply) and reply_to is None:
            reply_to = part.message_id
    return "".join(texts), image, reply_to


def _as_id(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def _as_mapping(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
