"""Platform-neutral form of an incoming message."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from verter.core.message.components import MessageComponent
from verter.core.platform.message_type import MessageType


@dataclass(slots=True)
class MessageMember:
    user_id: str
    nickname: str = ""


@dataclass(slots=True)
class Group:
    group_id: str
    group_name: str = ""


@dataclass(slots=True)
class BotMessage:
    type: MessageType
    self_id: str
    """The bot's own user id on this platform."""
    session_id: str
    message_id: str
    sender: MessageMember
    message: list[MessageComponent]
    message_str: str
    """Plain-text form of ``message``, used for command matching."""
    group: Group | None = None
    raw_message: object = None
    timestamp: float = field(default_factory=time.time)

    @property
    def group_id(self) -> str:
        return self.group.group_id if self.group else ""
