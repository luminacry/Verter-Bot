import asyncio
import gc
import importlib
import sys
import weakref
from pathlib import Path

import pytest

from tests import plugin_probe
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle
from verter.core.platform.sources.inmemory import InMemoryPlatform
from verter.core.star.plugin_manager import PluginManager

_ASK = """
from tests import plugin_probe
from verter.api.event import MessageEvent, filter
from verter.api.star import Star, register

@register("ask", "t", "ask", "1")
class Ask(Star):
    @filter.command("ask")
    async def ask(self, event: MessageEvent):
        yield event.plain_result("name?")
        plugin_probe.ready.set()
        follow = await event.wait_next(timeout=2)
        yield event.plain_result("got " + (follow.message_str if follow else "timeout"))
"""

_TICK = """
import asyncio
from tests import plugin_probe
from verter.api.event import MessageEvent, filter
from verter.api.star import Star, register

@register("tick", "t", "tick", "1")
class Tick(Star):
    async def initialize(self) -> None:
        self.context.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            plugin_probe.cancelled.append("done")

    @filter.command("tick")
    async def tick(self, event: MessageEvent):
        yield event.plain_result("tick")
"""

_PING = """
from verter.api.event import MessageEvent, filter
from verter.api.star import Context, Star, register

@register("ping", "t", "ping", "1")
class Ping(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    async def initialize(self) -> None:
        await self.context.put_kv("n", 1)

    @filter.command("ping")
    async def ping(self, event: MessageEvent):
        n = await self.context.get_kv("n", 0)
        yield event.plain_result(f"pong {n}")

    @filter.command("echo")
    async def echo(self, event: MessageEvent):
        await self.context.send_message(event.unified_msg_origin, "sent")
"""

_BROKEN = "raise RuntimeError('import boom')\n"

_GROUP = """
from verter.api.event import MessageEvent, filter
from verter.api.star import Star, register

tool = filter.command_group("tool")

@register("tool", "t", "tool", "1")
class Tool(Star):
    @tool.command("list")
    async def list_tools(self, event: MessageEvent):
        yield event.plain_result("listed")
"""


def _write(
    root: Path,
    name: str,
    source: str,
    *,
    metadata: bool = True,
    requirements: str | None = None,
) -> Path:
    path = root / "plugins" / name
    path.mkdir(parents=True)
    if metadata:
        (path / "metadata.yaml").write_text(
            f"name: {name}\nauthor: t\ndesc: {name}\nversion: 1\n", encoding="utf-8"
        )
    (path / "main.py").write_text(source, encoding="utf-8")
    if requirements is not None:
        (path / "requirements.txt").write_text(requirements, encoding="utf-8")
    return path


async def _boot(tmp_path: Path) -> tuple[CoreLifecycle, InMemoryPlatform]:
    cfg = VerterConfig()
    cfg.dashboard.enable = False
    cfg.computer.enable = False
    cfg.platform_settings.rate_limit.enable = False
    cfg.admins_id = ["root"]
    lifecycle = CoreLifecycle(
        cfg,
        data_dir=tmp_path,
        extra_platforms=[PlatformConfig(id="mem", type="inmemory")],
    )
    await lifecycle.start()
    platform = lifecycle.platforms.get("mem") if lifecycle.platforms else None
    assert isinstance(platform, InMemoryPlatform)
    return lifecycle, platform


async def _say(
    platform: InMemoryPlatform,
    text: str,
    *,
    sender_id: str = "user",
    group_id: str | None = None,
) -> list[str]:
    assert platform.inject(text, sender_id=sender_id, group_id=group_id) is not None
    sent: list[str] = []
    while True:
        try:
            message = await asyncio.wait_for(platform.sent.get(), 0.2)
        except TimeoutError:
            return sent
        sent.append(message.chain.outline())


async def test_console_commands_permissions_and_hot_reload(tmp_path: Path) -> None:
    lifecycle, platform = await _boot(tmp_path)
    try:
        assert "Hello, World!" in await _say(platform, "/helloworld")
        assert await _say(platform, "hello", group_id="g") == []
        help_text = "\n".join(await _say(platform, "/help"))
        assert "/helloworld" in help_text
        assert "/plugin" in help_text
        assert await _say(platform, "/sid") == ["mem:FriendMessage:user"]
        assert await _say(platform, "/plugin off helloworld") == ["只有管理员可以管理插件。"]
        assert await _say(platform, "/helloworld") == ["Hello, World!"]

        turned_off = await _say(platform, "/plugin off helloworld", sender_id="root")
        assert "已关闭 helloworld" in turned_off
        assert await _say(platform, "/helloworld") == []
        turned_on = await _say(platform, "/plugin on helloworld", sender_id="root")
        assert "已启用 helloworld" in turned_on
        assert await _say(platform, "/helloworld") == ["Hello, World!"]
        refused = await _say(platform, "/plugin off builtin_commands", sender_id="root")
        assert "不能关闭内置指令" in refused

        main = tmp_path / "plugins" / "helloworld" / "main.py"
        updated = main.read_text(encoding="utf-8").replace("Hello, World!", "Hello, again!")
        main.write_text(updated, encoding="utf-8")
        reloaded = await _say(platform, "/plugin reload helloworld", sender_id="root")
        assert "已重载 helloworld" in reloaded
        assert await _say(platform, "/helloworld") == ["Hello, again!"]
        listing = "\n".join(await _say(platform, "/plugin ls", sender_id="root"))
        assert "helloworld" in listing
        assert "on" in listing
    finally:
        await lifecycle.stop()


async def test_failed_plugin_does_not_block_others(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "helloworld").mkdir()
    _write(tmp_path, "ping", _PING)
    _write(tmp_path, "broken", _BROKEN)
    _write(
        tmp_path,
        "missingdep",
        "from verter.api.star import Star\n",
        requirements="not-a-real-verter-dep\n",
    )
    lifecycle, platform = await _boot(tmp_path)
    try:
        assert await _say(platform, "/ping") == ["pong 1"]
        assert await _say(platform, "/echo") == ["sent"]
        listing = "\n".join(await _say(platform, "/plugin ls", sender_id="root"))
        assert "broken" in listing
        assert "import boom" in listing
        assert "missing dependencies" in listing
    finally:
        await lifecycle.stop()


async def test_reload_drops_the_old_instance_and_cancels_tasks(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "helloworld").mkdir()
    _write(tmp_path, "tick", _TICK, metadata=False)
    plugin_probe.cancelled.clear()
    lifecycle, platform = await _boot(tmp_path)
    try:
        manager = lifecycle.plugins
        assert isinstance(manager, PluginManager)
        star = manager._records["tick"].star  # pyright: ignore[reportPrivateUsage]
        assert star is not None
        ref = weakref.ref(star)
        del star
        assert await _say(platform, "/tick") == ["tick"]
        assert "已重载 tick" in await _say(platform, "/plugin reload tick", sender_id="root")
        gc.collect()
        assert ref() is None
        assert plugin_probe.cancelled == ["done"]
        assert await _say(platform, "/tick") == ["tick"]
    finally:
        await lifecycle.stop()


async def test_wait_next_consumes_the_follow_up(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "helloworld").mkdir()
    _write(tmp_path, "ask", _ASK, metadata=False)
    _write(tmp_path, "tool", _GROUP, metadata=False)
    plugin_probe.ready = asyncio.Event()
    lifecycle, platform = await _boot(tmp_path)
    try:
        assert platform.inject("/ask") is not None
        first = await platform.next_message()
        assert first.chain.outline() == "name?"
        assert plugin_probe.ready is not None
        await plugin_probe.ready.wait()
        await asyncio.sleep(0)
        assert platform.inject("Ada") is not None
        second = await platform.next_message()
        assert second.chain.outline() == "got Ada"
        assert await _say(platform, "/tool list") == ["listed"]
        bus = lifecycle.event_bus
        assert bus is not None
        assert bus.stats.intercepted >= 1
    finally:
        await lifecycle.stop()


def test_there_is_no_astrbot_import_shim() -> None:
    sys.modules.pop("astrbot", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("astrbot")
