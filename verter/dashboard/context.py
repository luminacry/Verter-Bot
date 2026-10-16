"""State shared by dashboard routes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from verter.core.computer.runner import RunnerStatus
from verter.core.config import VerterConfig
from verter.core.event_bus import EventBus
from verter.core.log import logger
from verter.core.log_ring import RING, LogRing
from verter.core.security import generate_secret
from verter.core.star.models import PluginControl
from verter.dashboard.auth import LoginRateLimiter

if TYPE_CHECKING:
    from verter.dashboard.platforms import PlatformControl


@dataclass(slots=True)
class DashboardContext:
    config: VerterConfig
    jwt_secret: str
    runner: RunnerStatus
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=time.monotonic)
    login_limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)
    bus: EventBus | None = None
    plugins: PluginControl | None = None
    platforms: PlatformControl | None = None
    logs: LogRing = field(default_factory=lambda: RING)

    @classmethod
    def create(cls, config: VerterConfig, runner: RunnerStatus) -> DashboardContext:
        secret = config.dashboard.jwt_secret
        if not secret:
            logger.warning(
                "dashboard.jwt_secret is empty; using a temporary secret, "
                "so sessions end when the process restarts"
            )
            secret = generate_secret()
        return cls(config=config, jwt_secret=secret, runner=runner)
