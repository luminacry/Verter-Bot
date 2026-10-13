"""What a plugin is allowed to ask the runtime for."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from typing import Any

from verter.core.config import VerterConfig
from verter.core.db import Database
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_session import MessageSession
from verter.core.star.models import PluginControl, PluginInfo


class Context:
    """Handle passed to every :class:`~verter.core.star.star.Star`."""

    def __init__(
        self,
        config: VerterConfig,
        db: Database,
        plugins: PluginControl,
        plugin_name: str,
    ) -> None:
        self._config = config
        self._db = db
        self._plugins = plugins
        self._plugin_name = plugin_name
        self._tasks: set[asyncio.Task[object]] = set()

    @property
    def config(self) -> VerterConfig:
        return self._config

    def create_task[T](self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Track ``coro`` so disable, reload, and shutdown cancel it."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel_tasks(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def get_kv(self, key: str, default: Any = None) -> Any:
        return await self._db.kv_get(self._plugin_name, key, default)

    async def put_kv(self, key: str, value: Any) -> None:
        await self._db.kv_set(self._plugin_name, key, value)

    def command_names(self) -> Sequence[str]:
        return self._plugins.command_names()

    def describe_plugins(self) -> Sequence[PluginInfo]:
        return self._plugins.describe_plugins()

    async def set_plugin_enabled(self, name: str, enabled: bool) -> str:
        return await self._plugins.set_enabled(name, enabled)

    async def reload_plugin(self, name: str) -> str:
        return await self._plugins.reload(name)

    async def send_message(
        self, session: MessageSession | str, message: MessageChain | str
    ) -> None:
        """Send ``message`` to a conversation without an incoming event.

        ``session`` is a :class:`MessageSession` or its unified origin string.
        The platform adapter must already be running.
        """
        if isinstance(session, str):
            session = MessageSession.from_str(session)
        chain = message if isinstance(message, MessageChain) else MessageChain().message(message)
        from verter.core.runtime_state import current_runtime

        runtime = current_runtime.get()
        platforms = None if runtime is None else runtime.platforms
        if platforms is None:
            raise RuntimeError("platforms are not running")
        adapter = platforms.get(session.platform_id)
        if adapter is None:
            raise RuntimeError(f"platform {session.platform_id!r} is not running")
        await adapter.send_by_session(session, chain)
