"""Runtime metrics, the log ring, and plugin administration."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from verter.core.metrics import LatencyHistogram
from verter.core.star.models import PluginControl
from verter.dashboard.auth import ErrorResponse, SessionDependency

if TYPE_CHECKING:
    from verter.dashboard.context import DashboardContext

_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_STREAM_INTERVAL_S = 1.0


class _Disconnectable(Protocol):
    async def is_disconnected(self) -> bool: ...


class LatencyModel(BaseModel):
    buckets_us: list[int]
    counts: list[int]
    total: int
    sum_us: float


class RuntimeMetrics(BaseModel):
    received: int
    intercepted: int
    dropped: int
    processed: int
    failed: int
    timed_out: int
    pending: int
    in_flight: int
    latency: LatencyModel


class LogRecordModel(BaseModel):
    timestamp: str
    level: str
    logger: str
    message: str


class PluginModel(BaseModel):
    name: str
    author: str
    desc: str
    version: str
    enabled: bool
    error: str | None
    reserved: bool


class PluginEnabledBody(BaseModel):
    enabled: bool


class PluginAction(BaseModel):
    ok: bool
    message: str


def build_runtime_router(ctx: DashboardContext, require_session: SessionDependency) -> APIRouter:
    router = APIRouter()
    session = [Depends(require_session)]

    @router.get("/metrics", dependencies=session, responses={401: {"model": ErrorResponse}})
    async def metrics() -> RuntimeMetrics:
        return _metrics(ctx)

    @router.get(
        "/metrics/stream",
        dependencies=session,
        responses={401: {"model": ErrorResponse}},
    )
    async def metrics_stream(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _stream(ctx, request),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    @router.get("/logs", dependencies=session, responses={401: {"model": ErrorResponse}})
    async def logs(limit: int = Query(default=100, ge=1, le=500)) -> list[LogRecordModel]:
        return [
            LogRecordModel(
                timestamp=entry.timestamp,
                level=entry.level,
                logger=entry.logger,
                message=entry.message,
            )
            for entry in ctx.logs.snapshot(limit)
        ]

    @router.get("/plugins", dependencies=session, responses={401: {"model": ErrorResponse}})
    async def plugins() -> list[PluginModel]:
        return _plugins(ctx)

    @router.post(
        "/plugins/{name}/enabled",
        dependencies=session,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    async def set_enabled(name: str, body: PluginEnabledBody) -> PluginAction:
        plugins = _require_plugins(ctx)
        _check_name(name)
        message = await plugins.set_enabled(name, body.enabled)
        return PluginAction(ok=message.startswith("已"), message=message)

    @router.post(
        "/plugins/{name}/reload",
        dependencies=session,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    async def reload_plugin(name: str) -> PluginAction:
        plugins = _require_plugins(ctx)
        _check_name(name)
        message = await plugins.reload(name)
        return PluginAction(ok=message.startswith("已"), message=message)

    return router


def _metrics(ctx: DashboardContext) -> RuntimeMetrics:
    bus = ctx.bus
    histogram = bus.latency.snapshot() if bus is not None else LatencyHistogram().snapshot()
    stats = bus.stats if bus is not None else None
    return RuntimeMetrics(
        received=0 if stats is None else stats.received,
        intercepted=0 if stats is None else stats.intercepted,
        dropped=0 if stats is None else stats.dropped,
        processed=0 if stats is None else stats.processed,
        failed=0 if stats is None else stats.failed,
        timed_out=0 if stats is None else stats.timed_out,
        pending=0 if bus is None else bus.pending,
        in_flight=0 if bus is None else bus.in_flight,
        latency=LatencyModel(
            buckets_us=list(histogram.buckets_us),
            counts=list(histogram.counts),
            total=histogram.total,
            sum_us=histogram.sum_us,
        ),
    )


async def _stream(
    ctx: DashboardContext, request: _Disconnectable, *, interval_s: float = _STREAM_INTERVAL_S
) -> AsyncIterator[str]:
    while True:
        if await request.is_disconnected():
            return
        payload = _metrics(ctx).model_dump()
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        await asyncio.sleep(interval_s)


def _plugins(ctx: DashboardContext) -> list[PluginModel]:
    if ctx.plugins is None:
        return []
    return [
        PluginModel(
            name=info.name,
            author=info.author,
            desc=info.desc,
            version=info.version,
            enabled=info.enabled,
            error=info.error,
            reserved=info.reserved,
        )
        for info in ctx.plugins.describe_plugins()
    ]


def _require_plugins(ctx: DashboardContext) -> PluginControl:
    if ctx.plugins is None:
        raise HTTPException(503, "plugins_unavailable")
    return ctx.plugins


def _check_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        raise HTTPException(404, "plugin_not_found")
