"""Load, enable, and hot-reload plugins from disk. One failure stays inside that plugin."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import re
import shutil
import sys
import types
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from verter.core.config import VerterConfig
from verter.core.db import Database
from verter.core.log import logger
from verter.core.pipeline.context import BoundHandler, HookPoint
from verter.core.platform.message_event import MessageEvent
from verter.core.star.context import Context
from verter.core.star.filter import drop_handlers, handlers_in
from verter.core.star.handler_index import HandlerIndex, RegisteredHandler, bind_handler
from verter.core.star.models import PluginInfo
from verter.core.star.star import Star

_RESERVED = frozenset({"builtin_commands"})
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(slots=True)
class _Record:
    name: str
    path: Path
    author: str = ""
    desc: str = ""
    version: str = ""
    reserved: bool = False
    enabled: bool = True
    error: str | None = None
    active: bool = False
    module_name: str | None = None
    star: Star | None = None
    context: Context | None = None
    handlers: list[RegisteredHandler] = field(default_factory=list[RegisteredHandler])
    schema: dict[str, Any] | None = None


class PluginManager:
    """Handler source backed by the plugins currently enabled."""

    def __init__(self, config: VerterConfig, db: Database, data_dir: Path) -> None:
        self._config = config
        self._db = db
        self._data_dir = data_dir
        self._records: dict[str, _Record] = {}
        self._generation: dict[str, int] = {}
        self._index = HandlerIndex.build(())

    async def load(self) -> None:
        """Import built-in plugins and everything under ``data/plugins``."""
        seed_example(self._data_dir)
        for path in _builtin_dirs():
            await self._load_path(path, reserved=True)
        plugins = self._data_dir / "plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        user_plugins = sorted(
            path for path in plugins.iterdir() if path.is_dir() and not path.name.startswith(".")
        )
        for path in user_plugins:
            await self._load_path(path, reserved=path.name in _RESERVED)
        self._reindex()

    async def close(self) -> None:
        for record in list(self._records.values()):
            await self._deactivate(record)
            self._drop_module(record)
        self._records.clear()
        self._reindex()

    async def match(self, event: MessageEvent) -> Sequence[BoundHandler]:
        return self._index.match(event)

    async def run_hooks(self, hook: HookPoint, event: MessageEvent) -> None:
        for handler in self._index.hooks.get(hook, ()):
            if not handler.accepts(event):
                continue
            async for _ in handler.run(event):
                if event.is_stopped():
                    return
            if event.is_stopped():
                return

    def command_names(self) -> Sequence[str]:
        return self._index.command_names

    def describe_plugins(self) -> Sequence[PluginInfo]:
        return tuple(
            PluginInfo(
                name=record.name,
                author=record.author,
                desc=record.desc,
                version=record.version,
                enabled=record.enabled,
                error=record.error,
                reserved=record.reserved,
            )
            for record in self._records.values()
        )

    def index_size(self) -> int:
        """Enabled message handlers. Used by the dispatch benchmark."""
        return sum(len(record.handlers) for record in self._records.values() if record.enabled)

    async def set_enabled(self, name: str, enabled: bool) -> str:
        if name in _RESERVED and not enabled:
            return "不能关闭内置指令"
        record = self._records.get(name)
        if record is None:
            return f"未找到插件 {name}"
        if record.error and enabled:
            return f"插件不可用：{record.error}"
        record.enabled = enabled
        await self._db.set_plugin_enabled(name, enabled)
        if enabled and not record.active:
            await self._activate(record)
        elif not enabled and record.active:
            await self._deactivate(record)
        self._reindex()
        return f"已{'启用' if enabled else '关闭'} {name}"

    async def reload(self, name: str) -> str:
        record = self._records.get(name)
        if record is None:
            return f"未找到插件 {name}"
        path, reserved = record.path, record.reserved
        await self._deactivate(record)
        self._drop_module(record)
        self._records.pop(name, None)
        await self._load_path(path, reserved=reserved)
        self._reindex()
        loaded = self._records.get(name)
        if loaded is None or loaded.error:
            detail = loaded.error if loaded is not None else "unknown error"
            return f"重载失败：{detail}"
        return f"已重载 {name}"

    async def _load_path(self, path: Path, *, reserved: bool) -> None:
        record = _Record(name=path.name, path=path, reserved=reserved or path.name in _RESERVED)
        try:
            if not _NAME.fullmatch(path.name):
                raise ValueError(f"invalid plugin directory name {path.name!r}")
            missing = _missing_requirements(path)
            if missing:
                raise RuntimeError("missing dependencies: " + ", ".join(missing))
            record.schema = _read_schema(path)
            module_name = self._next_module_name(path.name)
            module = _import_module(module_name, path / "main.py")
            record.module_name = module_name
            meta = _metadata(path, module)
            if meta[0] != path.name:
                raise ValueError(
                    f"metadata name {meta[0]!r} does not match directory {path.name!r}"
                )
            record.name = meta[0]
            record.author = meta[1]
            record.desc = meta[2]
            record.version = meta[3]
            if record.name in self._records and self._records[record.name].error is None:
                raise ValueError(f"plugin {record.name!r} is already loaded")
            star_cls = _star_class(module)
            record.enabled = await self._db.plugin_enabled(record.name, default=True)
            record.context = Context(self._config, self._db, self, record.name)
            record.star = star_cls(record.context)
            record.handlers = [
                bind_handler(record.name, record.star, draft) for draft in handlers_in(module_name)
            ]
            if record.enabled:
                await self._activate(record)
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not stop the others
            logger.exception("Failed to load plugin %s", path.name)
            record.error = f"{type(exc).__name__}: {exc}"
            record.enabled = False
            if record.star is not None:
                await self._deactivate(record)
            self._drop_module(record)
        self._records[record.name] = record

    async def _activate(self, record: _Record) -> None:
        if record.star is None or record.error is not None:
            return
        await record.star.initialize()
        record.active = True

    async def _deactivate(self, record: _Record) -> None:
        record.active = False
        if record.star is not None:
            try:
                await record.star.terminate()
            except Exception:  # noqa: BLE001 - terminate failures are logged, then we still unload
                logger.exception("Plugin %s terminate failed", record.name)
        if record.context is not None:
            await record.context.cancel_tasks()

    def _drop_module(self, record: _Record) -> None:
        if record.module_name is not None:
            sys.modules.pop(record.module_name, None)
            drop_handlers(record.module_name)
            record.module_name = None
        record.star = None
        record.context = None
        record.handlers = []

    def _reindex(self) -> None:
        handlers: list[RegisteredHandler] = []
        for record in self._records.values():
            if record.enabled and record.error is None and record.active:
                handlers.extend(record.handlers)
        self._index = HandlerIndex.build(handlers)

    def _next_module_name(self, name: str) -> str:
        self._generation[name] = self._generation.get(name, 0) + 1
        return f"verter_plugin_{name}_{self._generation[name]}"


def seed_example(data_dir: Path) -> bool:
    """Copy the bundled helloworld plugin into ``data/plugins`` when it is absent."""
    source = _example_dir()
    if source is None:
        return False
    dest = data_dir / "plugins" / "helloworld"
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    return True


def _example_dir() -> Path | None:
    candidate = Path(__file__).resolve().parents[3] / "examples" / "plugins" / "helloworld"
    if (candidate / "main.py").is_file():
        return candidate
    return None


def _builtin_dirs() -> list[Path]:
    root = Path(__file__).resolve().parents[2] / "builtin_stars"
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if (path / "main.py").is_file())


def _import_module(module_name: str, path: Path) -> types.ModuleType:
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        drop_handlers(module_name)
        raise
    return module


def _metadata(path: Path, module: types.ModuleType) -> tuple[str, str, str, str]:
    file_meta = _yaml_meta(path / "metadata.yaml") if (path / "metadata.yaml").is_file() else None
    decorated = _decorated_meta(module)
    chosen = file_meta or decorated
    if chosen is None:
        raise ValueError("missing metadata.yaml and @register")
    return chosen


def _decorated_meta(module: types.ModuleType) -> tuple[str, str, str, str] | None:
    for obj in vars(module).values():
        meta = getattr(obj, "__verter_meta__", None)
        parsed = _four_strings(meta)
        if parsed is not None:
            return parsed
    return None


def _four_strings(value: object) -> tuple[str, str, str, str] | None:
    if not isinstance(value, tuple):
        return None
    raw = cast("tuple[object, ...]", value)
    if len(raw) < 4:
        return None
    parts: list[str] = []
    for item in raw[:4]:
        if not isinstance(item, str):
            return None
        parts.append(item)
    return (parts[0], parts[1], parts[2], parts[3])


def _yaml_meta(path: Path) -> tuple[str, str, str, str]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    loaded: object = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)  # noqa: S506
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must be a mapping")
    data = cast("dict[str, Any]", loaded)
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path.name} is missing name")
    author = _yaml_str(data.get("author", ""), path)
    desc = _yaml_str(data.get("desc", ""), path)
    version = _yaml_version(data.get("version", ""), path)
    return name, author, desc, version


def _yaml_str(value: object, path: Path) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{path.name} has a non-string field")


def _yaml_version(value: object, path: Path) -> str:
    """YAML reads an unquoted ``1`` as an int. Keep that as text."""
    if isinstance(value, bool):
        raise ValueError(f"{path.name} has a non-string field")
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    raise ValueError(f"{path.name} has a non-string field")


def _star_class(module: types.ModuleType) -> type[Star]:
    found = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Star) and obj.__module__ == module.__name__
    ]
    if len(found) != 1:
        raise ValueError(f"expected one Star subclass, found {len(found)}")
    return found[0]


def _missing_requirements(path: Path) -> list[str]:
    req = path / "requirements.txt"
    if not req.is_file():
        return []
    missing: list[str] = []
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        if not name:
            continue
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return missing


def _read_schema(path: Path) -> dict[str, Any] | None:
    schema = path / "_conf_schema.json"
    if not schema.is_file():
        return None
    loaded: object = json.loads(schema.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("_conf_schema.json must be a JSON object")
    return cast("dict[str, Any]", loaded)
