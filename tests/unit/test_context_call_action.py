"""Context.call_action: plugins reach platform actions such as NapCat's OneBot API."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

import pytest

from verter.core.config import VerterConfig
from verter.core.db import Database
from verter.core.platform.manager import PlatformManager
from verter.core.runtime_state import RuntimeHandle, current_runtime
from verter.core.star.context import Context
from verter.core.star.models import PluginControl


class _CallingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, action: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((action, dict(params or {})))
        return {"ok": action}


class _PlainAdapter:
    """An adapter without ``call``, like the console adapter."""


class _Platforms:
    def __init__(self, adapters: dict[str, object]) -> None:
        self._adapters = adapters

    def get(self, platform_id: str) -> object | None:
        return self._adapters.get(platform_id)


def _context() -> Context:
    return Context(
        VerterConfig(),
        cast("Database", object()),
        cast("PluginControl", object()),
        "test_plugin",
    )


@pytest.fixture
def runtime() -> Iterator[RuntimeHandle]:
    handle = RuntimeHandle()
    token = current_runtime.set(handle)
    try:
        yield handle
    finally:
        current_runtime.reset(token)


async def test_call_action_forwards_to_the_adapter(runtime: RuntimeHandle) -> None:
    adapter = _CallingAdapter()
    runtime.platforms = cast("PlatformManager", _Platforms({"qq": adapter}))
    data = await _context().call_action("qq", "set_group_ban", {"group_id": 1, "user_id": 2})
    assert data == {"ok": "set_group_ban"}
    assert adapter.calls == [("set_group_ban", {"group_id": 1, "user_id": 2})]


async def test_call_action_reports_missing_or_unsupported_platforms(
    runtime: RuntimeHandle,
) -> None:
    context = _context()
    with pytest.raises(RuntimeError, match="platforms are not running"):
        await context.call_action("qq", "get_status")

    runtime.platforms = cast("PlatformManager", _Platforms({"console": _PlainAdapter()}))
    with pytest.raises(RuntimeError, match="'qq' is not running"):
        await context.call_action("qq", "get_status")
    with pytest.raises(RuntimeError, match="does not support actions"):
        await context.call_action("console", "get_status")
