"""Base class for platform adapters."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from verter.core.config import ConfigError, PlatformConfig
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.platform_metadata import PlatformMetadata


class PlatformStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"


class EventSink(Protocol):
    def commit(self, event: MessageEvent) -> bool:
        """Queue ``event`` for processing; returns False if it was dropped."""
        ...


@dataclass(frozen=True, slots=True)
class PlatformContext:
    """What the runtime gives every adapter."""

    sink: EventSink
    request_shutdown: Callable[[], None]


class Platform(ABC):
    """One running adapter instance.

    ``run()`` receives messages until ``terminate()`` is called. If it raises,
    the platform manager restarts it with backoff.
    """

    def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
        self.config = config
        self.context = context
        self.status = PlatformStatus.PENDING
        self.last_error: str | None = None

    @abstractmethod
    def meta(self) -> PlatformMetadata: ...

    @abstractmethod
    async def run(self) -> None: ...

    async def terminate(self) -> None:
        return None

    @abstractmethod
    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        """Send a message to a conversation without an incoming event, e.g. from a plugin."""

    def commit_event(self, event: MessageEvent) -> bool:
        return self.context.sink.commit(event)

    def parse_settings[M: BaseModel](self, model: type[M]) -> M:
        """Validate the adapter-specific keys of this platform's config entry.

        A string value written as ``ENV:NAME`` is read from the environment variable
        ``NAME``, so secrets such as tokens can stay out of ``config.yaml``.

        Raises:
            ConfigError: If the keys do not match ``model`` or a referenced variable is unset.
        """
        settings = {
            key: _resolve_env(self.config.id, key, value)
            for key, value in self.config.settings.items()
        }
        try:
            return model.model_validate(settings)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            raise ConfigError(f"platform {self.config.id!r}: {details}") from exc


_ENV_PREFIX = "ENV:"


def _resolve_env(platform_id: str, key: str, value: Any) -> Any:
    """Replace an ``ENV:NAME`` string with the value of environment variable ``NAME``.

    Raises:
        ConfigError: If the variable name is empty or the variable is not set.
    """
    if not isinstance(value, str) or not value.startswith(_ENV_PREFIX):
        return value
    name = value[len(_ENV_PREFIX) :].strip()
    if not name:
        raise ConfigError(f"platform {platform_id!r}: {key}: empty environment variable name")
    resolved = os.environ.get(name)
    if resolved is None:
        raise ConfigError(
            f"platform {platform_id!r}: {key}: environment variable {name!r} is not set"
        )
    return resolved
