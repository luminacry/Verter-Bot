"""Adapter registry. Built-in adapters are imported only when a config entry uses them."""

from __future__ import annotations

import importlib
from collections.abc import Callable

from verter.core.config import ConfigError
from verter.core.platform.platform import Platform

_BUILTIN: dict[str, str] = {
    "console": "verter.core.platform.sources.console:ConsolePlatform",
    "inmemory": "verter.core.platform.sources.inmemory:InMemoryPlatform",
    "onebot_v11": "verter.core.platform.sources.onebot_v11:OneBotV11Platform",
}
_registered: dict[str, type[Platform]] = {}


def register_platform_adapter[P: Platform](name: str) -> Callable[[type[P]], type[P]]:
    """Class decorator that makes an adapter available as ``type: <name>`` in the config."""

    def decorator(cls: type[P]) -> type[P]:
        existing = _registered.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"platform adapter {name!r} is already registered")
        _registered[name] = cls
        return cls

    return decorator


def unregister_platform_adapter(name: str) -> None:
    _registered.pop(name, None)


def get_platform_adapter(name: str) -> type[Platform]:
    """Resolve an adapter class by its config ``type``.

    Raises:
        ConfigError: If no adapter has that name.
    """
    if name in _registered:
        return _registered[name]
    target = _BUILTIN.get(name)
    if target is None:
        known = ", ".join(sorted({*_BUILTIN, *_registered}))
        raise ConfigError(f"unknown platform type {name!r} (known: {known})")
    module_name, _, attribute = target.partition(":")
    adapter: object = getattr(importlib.import_module(module_name), attribute)
    if not (isinstance(adapter, type) and issubclass(adapter, Platform)):
        raise TypeError(f"{target} is not a Platform subclass")
    return adapter
