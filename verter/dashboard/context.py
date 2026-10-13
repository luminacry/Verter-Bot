"""State shared by dashboard routes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from verter.core.computer.runner import RunnerStatus
from verter.core.config import VerterConfig
from verter.core.log import logger
from verter.core.security import generate_secret
from verter.dashboard.auth import LoginRateLimiter


@dataclass(slots=True)
class DashboardContext:
    config: VerterConfig
    jwt_secret: str
    runner: RunnerStatus
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=time.monotonic)
    login_limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)

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
