"""Health and system information endpoints."""

import platform
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from verter import __version__
from verter.dashboard.auth import ErrorResponse, SessionDependency

if TYPE_CHECKING:
    from verter.dashboard.context import DashboardContext


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class SandboxBackend(BaseModel):
    kind: str
    available: bool
    detail: str


class SandboxRunner(BaseModel):
    available: bool
    version: str | None
    backends: list[SandboxBackend]
    error: str | None


class SandboxPolicy(BaseModel):
    enabled: bool
    backend: Literal["local"]
    allow: Literal["admins", "whitelist", "all"]
    network: Literal["deny", "allow"]


class SandboxInfo(BaseModel):
    policy: SandboxPolicy
    runner: SandboxRunner


class SystemInfo(BaseModel):
    version: str
    python_version: str
    os: str
    arch: str
    platform: str
    started_at: datetime
    uptime_seconds: float
    sandbox: SandboxInfo


def build_system_router(ctx: "DashboardContext", require_session: SessionDependency) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> HealthResponse:
        return HealthResponse()

    @router.get(
        "/system/info",
        dependencies=[Depends(require_session)],
        responses={401: {"model": ErrorResponse}},
    )
    async def system_info() -> SystemInfo:
        policy = ctx.config.computer
        runner = ctx.runner
        return SystemInfo(
            version=__version__,
            python_version=platform.python_version(),
            os="windows" if sys.platform == "win32" else sys.platform,
            arch=platform.machine().lower(),
            platform=platform.platform(),
            started_at=ctx.started_at,
            uptime_seconds=round(time.monotonic() - ctx.started_monotonic, 3),
            sandbox=SandboxInfo(
                policy=SandboxPolicy(
                    enabled=policy.enable,
                    backend=policy.backend,
                    allow=policy.allow,
                    network=policy.network,
                ),
                runner=SandboxRunner(
                    available=runner.available,
                    version=runner.version,
                    backends=[
                        SandboxBackend(kind=b.kind, available=b.available, detail=b.detail)
                        for b in runner.backends
                    ],
                    error=runner.error,
                ),
            ),
        )

    return router
