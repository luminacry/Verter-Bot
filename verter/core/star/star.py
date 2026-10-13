"""Base class of a plugin, and the decorator that records its metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verter.core.star.context import Context


class Star:
    """One loaded plugin instance.

    Subclasses register handlers with ``filter`` decorators. ``initialize`` runs
    when the plugin is enabled; ``terminate`` runs on disable, reload, and shutdown.
    """

    __verter_meta__: tuple[str, str, str, str, str] | None = None
    context: Context

    def __init__(self, context: Context) -> None:
        self.context = context

    async def initialize(self) -> None:
        return None

    async def terminate(self) -> None:
        return None


def register(name: str, author: str, desc: str, version: str, repo: str = ""):
    """Attach display metadata to a :class:`Star` subclass.

    ``metadata.yaml`` wins when both are present. ``repo`` is optional.
    """

    def decorator[S: Star](cls: type[S]) -> type[S]:
        cls.__verter_meta__ = (name, author, desc, version, repo)
        return cls

    return decorator
