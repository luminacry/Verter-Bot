"""Shared plugin records. Kept separate so Context and PluginManager do not import each other."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PluginInfo:
    name: str
    author: str
    desc: str
    version: str
    enabled: bool
    error: str | None
    reserved: bool


class PluginControl(Protocol):
    def command_names(self) -> Sequence[str]: ...

    def describe_plugins(self) -> Sequence[PluginInfo]: ...

    async def set_enabled(self, name: str, enabled: bool) -> str: ...

    async def reload(self, name: str) -> str: ...
