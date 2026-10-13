"""Dashboard HTTP application: JSON API under ``/api`` plus the built WebUI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from verter import __version__
from verter.core.computer.runner import RunnerStatus
from verter.core.config import ConfigError, VerterConfig
from verter.dashboard.auth import build_auth_router, make_session_dependency
from verter.dashboard.context import DashboardContext
from verter.dashboard.system import build_system_router

ENV_WEBUI_DIST = "VERTER_WEBUI_DIST"
PACKAGED_WEBUI = Path(__file__).parent / "static"

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache"
_CSP = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "script-src 'self'",
        "connect-src 'self'",
    )
)
_SECURITY_HEADERS = (
    ("content-security-policy", _CSP),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "same-origin"),
)


class SecurityHeadersMiddleware:
    """Adds security headers to every response and disables caching of API responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = str(scope["path"]).startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS:
                    headers.setdefault(name, value)
                if is_api:
                    headers.setdefault("cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(ctx: DashboardContext, *, webui_dist: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Verter-Bot Dashboard API",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    require_session = make_session_dependency(ctx)
    app.include_router(build_auth_router(ctx, require_session), prefix="/api/auth", tags=["auth"])
    app.include_router(build_system_router(ctx, require_session), prefix="/api", tags=["system"])
    if webui_dist is not None:
        _mount_webui(app, webui_dist)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


def openapi_schema() -> dict[str, Any]:
    """OpenAPI document consumed by the WebUI client generator."""
    ctx = DashboardContext(
        config=VerterConfig(),
        jwt_secret="openapi-export",  # noqa: S106 - never signs a real session
        runner=RunnerStatus(available=False),
    )
    return create_app(ctx).openapi()


def resolve_webui_dist(explicit: str | Path | None = None) -> Path | None:
    """Find built WebUI assets: explicit path, ``VERTER_WEBUI_DIST``, or the packaged copy.

    Raises:
        ConfigError: If an explicitly requested directory has no ``index.html``.
    """
    requested = explicit or os.environ.get(ENV_WEBUI_DIST)
    if requested:
        path = Path(requested).expanduser().resolve()
        if not (path / "index.html").is_file():
            raise ConfigError(f"WebUI directory {path} does not contain index.html")
        return path
    if (PACKAGED_WEBUI / "index.html").is_file():
        return PACKAGED_WEBUI
    return None


def _mount_webui(app: FastAPI, dist: Path) -> None:
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def webui(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not_found")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                cache = _IMMUTABLE if path.startswith("assets/") else _NO_CACHE
                return FileResponse(candidate, headers={"cache-control": cache})
        return FileResponse(index, headers={"cache-control": _NO_CACHE})
