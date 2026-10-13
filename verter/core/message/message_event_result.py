"""Outgoing message chains and handler results."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import Enum, auto
from pathlib import Path
from typing import Self

from verter.core.message.components import At, AtAll, Image, MessageComponent, Plain


class MessageChain:
    """An ordered list of message components with fluent builders."""

    __slots__ = ("chain",)

    def __init__(self, chain: Iterable[MessageComponent] = ()) -> None:
        self.chain: list[MessageComponent] = list(chain)

    def message(self, text: str) -> Self:
        self.chain.append(Plain(text))
        return self

    def at(self, target: str, name: str = "") -> Self:
        self.chain.append(At(target, name))
        return self

    def at_all(self) -> Self:
        self.chain.append(AtAll())
        return self

    def url_image(self, url: str) -> Self:
        self.chain.append(Image.from_url(url))
        return self

    def file_image(self, path: str | Path) -> Self:
        self.chain.append(Image.from_path(path))
        return self

    def base64_image(self, data: str | bytes) -> Self:
        self.chain.append(Image.from_base64(data))
        return self

    def append(self, component: MessageComponent) -> Self:
        self.chain.append(component)
        return self

    def plain_text(self) -> str:
        """Concatenated text of the :class:`Plain` components."""
        return "".join(c.text for c in self.chain if isinstance(c, Plain))

    get_plain_text = plain_text

    def outline(self) -> str:
        return "".join(c.outline() for c in self.chain)

    def __iter__(self) -> Iterator[MessageComponent]:
        return iter(self.chain)

    def __len__(self) -> int:
        return len(self.chain)

    def __bool__(self) -> bool:
        return bool(self.chain)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MessageChain) and self.chain == other.chain

    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.chain!r})"


class EventResultType(Enum):
    CONTINUE = auto()
    STOP = auto()


class MessageEventResult(MessageChain):
    """A reply produced by a handler. ``stop_event()`` also stops the event once it is set."""

    __slots__ = ("result_type",)

    def __init__(self, chain: Iterable[MessageComponent] = ()) -> None:
        super().__init__(chain)
        self.result_type = EventResultType.CONTINUE

    def stop_event(self) -> Self:
        self.result_type = EventResultType.STOP
        return self

    def continue_event(self) -> Self:
        self.result_type = EventResultType.CONTINUE
        return self

    def is_stopped(self) -> bool:
        return self.result_type is EventResultType.STOP
