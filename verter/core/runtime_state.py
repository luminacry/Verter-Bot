"""Mutable handles copied into worker tasks at creation time."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verter.core.event_bus import EventBus
    from verter.core.platform.manager import PlatformManager


@dataclass(slots=True)
class RuntimeHandle:
    """Shared with every event-bus worker. Fields are filled in as startup proceeds."""

    platforms: PlatformManager | None = None
    bus: EventBus | None = None


current_runtime: contextvars.ContextVar[RuntimeHandle | None] = contextvars.ContextVar(
    "verter_runtime", default=None
)
