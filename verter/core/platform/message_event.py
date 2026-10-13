"""The event object carried through the pipeline.

Method names follow AstrBot's message event API so plugins port with renames only.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Literal

from verter.core.message.components import MessageComponent
from verter.core.message.message_event_result import MessageChain, MessageEventResult
from verter.core.platform.bot_message import BotMessage
from verter.core.platform.message_type import MessageType
from verter.core.platform.platform_metadata import PlatformMetadata

if TYPE_CHECKING:
    from verter.core.pipeline.context import BoundHandler


class MessageEvent:
    """A message received from a platform.

    ``stop_event()`` stops later handlers and stages; a result a handler has
    already yielded is still decorated and sent.
    """

    __slots__ = (
        "_extras",
        "_has_send_oper",
        "_result",
        "_stopped",
        "activated_handlers",
        "call_llm",
        "created_at",
        "is_at_or_wake_command",
        "is_wake",
        "message_obj",
        "message_str",
        "platform_meta",
        "role",
        "session_id",
    )

    def __init__(
        self,
        message_str: str,
        message_obj: BotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
    ) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.session_id = session_id
        self.role: Literal["admin", "member"] = "member"
        self.is_wake = False
        self.is_at_or_wake_command = False
        self.call_llm = True
        self.activated_handlers: Sequence[BoundHandler] = ()
        self.created_at = time.perf_counter()
        self._result: MessageEventResult | None = None
        self._stopped = False
        self._extras: dict[str, Any] = {}
        self._has_send_oper = False

    @property
    def unified_msg_origin(self) -> str:
        """``platform_id:MessageType:session_id``; identifies the conversation."""
        return f"{self.platform_meta.id}:{self.message_obj.type.value}:{self.session_id}"

    @property
    def has_sent(self) -> bool:
        return self._has_send_oper

    def get_platform_name(self) -> str:
        return self.platform_meta.name

    def get_platform_id(self) -> str:
        return self.platform_meta.id

    def get_message_str(self) -> str:
        return self.message_str

    def get_messages(self) -> list[MessageComponent]:
        return self.message_obj.message

    def get_message_outline(self) -> str:
        return "".join(c.outline() for c in self.message_obj.message)

    def get_message_type(self) -> MessageType:
        return self.message_obj.type

    def get_session_id(self) -> str:
        return self.session_id

    def get_group_id(self) -> str:
        return self.message_obj.group_id

    def get_self_id(self) -> str:
        return self.message_obj.self_id

    def get_sender_id(self) -> str:
        return self.message_obj.sender.user_id

    def get_sender_name(self) -> str:
        return self.message_obj.sender.nickname

    def is_private_chat(self) -> bool:
        return self.message_obj.type is MessageType.FRIEND_MESSAGE

    def is_wake_up(self) -> bool:
        return self.is_wake

    def is_admin(self) -> bool:
        return self.role == "admin"

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extras.get(key, default)

    def clear_extra(self) -> None:
        self._extras.clear()

    def set_result(self, result: MessageEventResult | str) -> None:
        if isinstance(result, str):
            result = MessageEventResult().message(result)
        self._result = result
        if result.is_stopped():
            self._stopped = True

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def clear_result(self) -> None:
        self._result = None

    def stop_event(self) -> None:
        self._stopped = True

    def continue_event(self) -> None:
        self._stopped = False

    def is_stopped(self) -> bool:
        return self._stopped

    def should_call_llm(self, call_llm: bool) -> None:
        self.call_llm = call_llm

    def make_result(self) -> MessageEventResult:
        return MessageEventResult()

    def plain_result(self, text: str) -> MessageEventResult:
        return MessageEventResult().message(text)

    def image_result(self, url_or_path: str) -> MessageEventResult:
        if url_or_path.startswith(("http://", "https://")):
            return MessageEventResult().url_image(url_or_path)
        return MessageEventResult().file_image(url_or_path)

    def chain_result(self, chain: Iterable[MessageComponent]) -> MessageEventResult:
        return MessageEventResult(chain)

    async def send(self, message: MessageChain) -> None:
        """Send ``message`` to this event's conversation."""
        self._has_send_oper = True
        await self._send(message)

    async def _send(self, message: MessageChain) -> None:
        raise NotImplementedError
