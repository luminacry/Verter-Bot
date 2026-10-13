"""Doctor capabilities and sandbox audit for the dashboard."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from verter.core.computer.runner import resolve_runner_binary
from verter.dashboard.auth import ErrorResponse, SessionDependency

if TYPE_CHECKING:
    from verter.dashboard.context import DashboardContext


class DoctorBackend(BaseModel):
    kind: str
    available: bool
    detail: str


class DoctorModel(BaseModel):
    os: str
    arch: str
    backends: list[DoctorBackend]
    auto: str
    policy: str
    can_execute: bool
    error: str | None = None


class AuditModel(BaseModel):
    id: int
    created_at: str
    session_id: str
    initiator: str
    command: str
    allowed: bool
    exit_code: int | None
    timed_out: bool | None
    backend: str | None
    wall_ms: int | None
    truncated: bool
    output: str | None
    error: str | None


def build_sandbox_router(ctx: DashboardContext, require_session: SessionDependency) -> APIRouter:
    router = APIRouter()
    session = [Depends(require_session)]

    @router.get("/sandbox/doctor", dependencies=session, responses={401: {"model": ErrorResponse}})
    async def doctor() -> DoctorModel:
        return await _doctor(ctx)

    @router.get("/sandbox/audit", dependencies=session, responses={401: {"model": ErrorResponse}})
    async def audit(limit: int = Query(default=100, ge=1, le=500)) -> list[AuditModel]:
        if ctx.db is None:
            return []
        return [
            AuditModel(
                id=row.id,
                created_at=row.created_at,
                session_id=row.session_id,
                initiator=row.initiator,
                command=row.command,
                allowed=row.allowed,
                exit_code=row.exit_code,
                timed_out=row.timed_out,
                backend=row.backend,
                wall_ms=row.wall_ms,
                truncated=row.truncated,
                output=row.output,
                error=row.error,
            )
            for row in await ctx.db.list_sandbox_audit(limit)
        ]

    return router


async def _doctor(ctx: DashboardContext) -> DoctorModel:
    binary = ctx.runner.path or resolve_runner_binary()
    if binary is None:
        return DoctorModel(
            os="windows" if sys.platform == "win32" else sys.platform,
            arch="",
            backends=[],
            auto="none",
            policy="exec refuses to run without isolation",
            can_execute=False,
            error="verter-sandbox not found",
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "doctor",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, TimeoutError) as exc:
        return DoctorModel(
            os="windows" if sys.platform == "win32" else sys.platform,
            arch="",
            backends=[],
            auto="none",
            policy="exec refuses to run without isolation",
            can_execute=False,
            error=str(exc),
        )
    if proc.returncode != 0:
        raise HTTPException(503, stderr.decode("utf-8", errors="replace") or "doctor_failed")
    raw: object = json.loads(stdout.decode("utf-8"))
    if not isinstance(raw, dict):
        raise HTTPException(503, "doctor_failed")
    payload = cast("dict[str, object]", raw)
    backends_obj = payload.get("backends")
    backends: list[DoctorBackend] = []
    if isinstance(backends_obj, list):
        for raw_item in cast("list[object]", backends_obj):
            if not isinstance(raw_item, dict):
                continue
            item = cast("dict[str, object]", raw_item)
            backends.append(
                DoctorBackend(
                    kind=str(item.get("kind", "")),
                    available=bool(item.get("available")),
                    detail=str(item.get("detail", "")),
                )
            )
    auto = str(payload.get("auto", "none"))
    return DoctorModel(
        os=str(payload.get("os", "")),
        arch=str(payload.get("arch", "")),
        backends=backends,
        auto=auto,
        policy=str(payload.get("policy", "exec refuses to run without isolation")),
        can_execute=auto != "none",
        error=None,
    )
