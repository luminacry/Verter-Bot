"""Message segments. Platform adapters convert native payloads to and from these."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar


class ComponentType(StrEnum):
    PLAIN = "plain"
    AT = "at"
    AT_ALL = "at_all"
    IMAGE = "image"
    REPLY = "reply"
    FACE = "face"
    RECORD = "record"
    VIDEO = "video"
    FILE = "file"
    JSON = "json"
    FORWARD = "forward"
    NODE = "node"
    MUSIC = "music"
    CONTACT = "contact"
    POKE = "poke"
    DICE = "dice"
    RPS = "rps"
    MFACE = "mface"
    UNKNOWN = "unknown"


class MessageComponent:
    """Base class of all message segments."""

    __slots__ = ()
    type: ClassVar[ComponentType]

    def outline(self) -> str:
        """Short text form for logs and previews."""
        raise NotImplementedError


@dataclass(slots=True)
class Plain(MessageComponent):
    text: str
    type: ClassVar[ComponentType] = ComponentType.PLAIN

    def outline(self) -> str:
        return self.text


@dataclass(slots=True)
class At(MessageComponent):
    """Mention of one user, identified by their platform user id."""

    target: str
    name: str = ""
    type: ClassVar[ComponentType] = ComponentType.AT

    def outline(self) -> str:
        return f"@{self.name or self.target}"


@dataclass(slots=True)
class AtAll(MessageComponent):
    type: ClassVar[ComponentType] = ComponentType.AT_ALL

    def outline(self) -> str:
        return "@all"


def _local_uri(path: str | Path) -> str:
    return Path(path).resolve().as_uri()


def _base64_uri(data: str | bytes) -> str:
    text = base64.b64encode(data).decode("ascii") if isinstance(data, bytes) else data
    return f"base64://{text}"


@dataclass(slots=True)
class Image(MessageComponent):
    """An image referenced by ``http(s)://``, ``file://``, or ``base64://`` URI."""

    file: str
    type: ClassVar[ComponentType] = ComponentType.IMAGE

    @classmethod
    def from_url(cls, url: str) -> Image:
        return cls(url)

    @classmethod
    def from_path(cls, path: str | Path) -> Image:
        return cls(_local_uri(path))

    @classmethod
    def from_base64(cls, data: str | bytes) -> Image:
        return cls(_base64_uri(data))

    def outline(self) -> str:
        return "[image]"


@dataclass(slots=True)
class Record(MessageComponent):
    """A voice message, referenced like :class:`Image`."""

    file: str
    type: ClassVar[ComponentType] = ComponentType.RECORD

    @classmethod
    def from_path(cls, path: str | Path) -> Record:
        return cls(_local_uri(path))

    def outline(self) -> str:
        return "[voice]"


@dataclass(slots=True)
class Video(MessageComponent):
    """A video, referenced like :class:`Image`."""

    file: str
    type: ClassVar[ComponentType] = ComponentType.VIDEO

    def outline(self) -> str:
        return "[video]"


@dataclass(slots=True)
class File(MessageComponent):
    name: str
    file: str = ""
    type: ClassVar[ComponentType] = ComponentType.FILE

    def outline(self) -> str:
        return f"[file:{self.name}]"


@dataclass(slots=True)
class Json(MessageComponent):
    """A JSON card; ``data`` is the raw JSON string."""

    data: str
    type: ClassVar[ComponentType] = ComponentType.JSON

    def outline(self) -> str:
        return "[json]"


@dataclass(slots=True)
class Forward(MessageComponent):
    """A merged-forward message, referenced by its id."""

    id: str
    type: ClassVar[ComponentType] = ComponentType.FORWARD

    def outline(self) -> str:
        return "[forward]"


@dataclass(slots=True)
class Node(MessageComponent):
    """One node of a merged-forward message being sent."""

    content: list[MessageComponent] = field(default_factory=list[MessageComponent])
    user_id: str = ""
    nickname: str = ""
    message_id: str = ""
    type: ClassVar[ComponentType] = ComponentType.NODE

    def outline(self) -> str:
        return "[node]"


@dataclass(slots=True)
class Music(MessageComponent):
    """Music share. ``kind`` is ``qq``, ``163``, ``custom`` and so on."""

    kind: str
    id: str = ""
    url: str = ""
    audio: str = ""
    title: str = ""
    image: str = ""
    singer: str = ""
    type: ClassVar[ComponentType] = ComponentType.MUSIC

    def outline(self) -> str:
        return f"[music:{self.title or self.kind}]"


@dataclass(slots=True)
class Contact(MessageComponent):
    """A recommended friend (``kind='qq'``) or group (``kind='group'``)."""

    kind: str
    id: str
    type: ClassVar[ComponentType] = ComponentType.CONTACT

    def outline(self) -> str:
        return f"[contact:{self.kind}]"


@dataclass(slots=True)
class Poke(MessageComponent):
    """A poke (戳一戳). ``target`` is the poked user id when known."""

    target: str = ""
    type: ClassVar[ComponentType] = ComponentType.POKE

    def outline(self) -> str:
        return "[poke]"


@dataclass(slots=True)
class Dice(MessageComponent):
    result: str = ""
    type: ClassVar[ComponentType] = ComponentType.DICE

    def outline(self) -> str:
        return f"[dice:{self.result}]" if self.result else "[dice]"


@dataclass(slots=True)
class Rps(MessageComponent):
    """Rock-paper-scissors."""

    result: str = ""
    type: ClassVar[ComponentType] = ComponentType.RPS

    def outline(self) -> str:
        return f"[rps:{self.result}]" if self.result else "[rps]"


@dataclass(slots=True)
class Mface(MessageComponent):
    """A market emoji pack sticker."""

    emoji_id: str = ""
    emoji_package_id: str = ""
    key: str = ""
    summary: str = ""
    type: ClassVar[ComponentType] = ComponentType.MFACE

    def outline(self) -> str:
        return f"[mface:{self.summary}]" if self.summary else "[mface]"


@dataclass(slots=True)
class Reply(MessageComponent):
    """Quote of an earlier message."""

    message_id: str
    sender_id: str = ""
    text: str = ""
    type: ClassVar[ComponentType] = ComponentType.REPLY

    def outline(self) -> str:
        return "[reply]"


@dataclass(slots=True)
class Face(MessageComponent):
    """A platform emoji or sticker identified by its platform id."""

    id: str
    type: ClassVar[ComponentType] = ComponentType.FACE

    def outline(self) -> str:
        return f"[face:{self.id}]"


@dataclass(slots=True)
class Unknown(MessageComponent):
    """A segment the adapter does not model; ``data`` keeps the native fields."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict[str, Any])
    type: ClassVar[ComponentType] = ComponentType.UNKNOWN

    def outline(self) -> str:
        return f"[{self.kind}]"
