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
    FILE = "file"
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
class File(MessageComponent):
    name: str
    file: str = ""
    type: ClassVar[ComponentType] = ComponentType.FILE

    def outline(self) -> str:
        return f"[file:{self.name}]"


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
