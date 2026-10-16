"""Platform status and per-adapter restart."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from verter.core.log import logger
from verter.core.platform.manager import PlatformStat
from verter.dashboard.auth import ErrorResponse, SessionDependency, SessionInfo

if TYPE_CHECKING:
    from verter.dashboard.context import DashboardContext


class PlatformControl(Protocol):
    def stats(self) -> list[PlatformStat]: ...

    def get(self, platform_id: str) -> object | None: ...

    async def restart(self, platform_id: str) -> None: ...


_MAX_ERROR = 500
_SECRET_KEYS = ("token", "access_token")

PlatformState = Literal["starting", "running", "backoff", "stopped"]


class PlatformModel(BaseModel):
    id: str
    type: str
    status: PlatformState
    restarts: int
    last_error: str | None
    next_restart_in_s: float | None
    online: bool | None
    """None when the adapter has no notion of being online, such as the console."""
    last_seen_ago_s: float | None


def build_platforms_router(ctx: DashboardContext, require_session: SessionDependency) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/platforms",
        dependencies=[Depends(require_session)],
        responses={401: {"model": ErrorResponse}},
    )
    async def platforms() -> list[PlatformModel]:
        return _describe(ctx)

    @router.post(
        "/platforms/{platform_id}/restart",
        dependencies=[Depends(require_session)],
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def restart(
        platform_id: str,
        session: SessionInfo = Depends(require_session),  # noqa: B008
    ) -> PlatformModel:
        manager = ctx.platforms
        if manager is None:
            raise HTTPException(503, "platforms_unavailable")
        logger.info("user %s restarted platform %s", session.username, platform_id)
        try:
            await manager.restart(platform_id)
        except KeyError:
            raise HTTPException(404, "not_found") from None
        except RuntimeError as exc:
            status = 503 if "stopping" in str(exc) else 409
            raise HTTPException(status, "restarting") from None
        described = _describe(ctx)
        return next(item for item in described if item.id == platform_id)

    return router


def _describe(ctx: DashboardContext) -> list[PlatformModel]:
    manager = ctx.platforms
    if manager is None:
        return []
    return _from_manager(manager, _secrets(ctx), _loop_time())


def _from_manager(manager: PlatformControl, secrets: list[str], now: float) -> list[PlatformModel]:
    models: list[PlatformModel] = []
    for stat in manager.stats():
        adapter = manager.get(stat.id)
        online, last_seen = _presence(adapter, now)
        remaining = None if stat.next_restart_at is None else max(0.0, stat.next_restart_at - now)
        models.append(
            PlatformModel(
                id=stat.id,
                type=stat.type,
                status=stat.status.value,  # type: ignore[arg-type]
                restarts=stat.restarts,
                last_error=_redact(stat.last_error, secrets),
                next_restart_in_s=remaining,
                online=online,
                last_seen_ago_s=None if last_seen is None else max(0.0, now - last_seen),
            )
        )
    return models


def _presence(adapter: object | None, now: float) -> tuple[bool | None, float | None]:
    """Online flag and the loop time it was last confirmed, for adapters that track it."""
    if adapter is None:
        return None, None
    online = getattr(adapter, "online", None)
    if not isinstance(online, bool):
        return None, None
    heartbeat = getattr(adapter, "last_heartbeat", None)
    return online, heartbeat if isinstance(heartbeat, float) else None


def _secrets(ctx: DashboardContext) -> list[str]:
    found: list[str] = []
    for platform in ctx.config.platform:
        for key in _SECRET_KEYS:
            value = platform.settings.get(key)
            if isinstance(value, str) and value and not value.startswith("ENV:"):
                found.append(value)
    return found


def _redact(message: str | None, secrets: list[str]) -> str | None:
    """Hide configured tokens and trim, so an error cannot leak credentials."""
    if message is None:
        return None
    for secret in secrets:
        message = message.replace(secret, "<redacted>")
    message = re.sub(r"/bot[^/\s]+", "/bot<redacted>", message)
    return message[:_MAX_ERROR]


def _loop_time() -> float:
    import asyncio

    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return 0.0
