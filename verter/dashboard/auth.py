"""Dashboard authentication: session cookies, login, and failed-login throttling."""

import asyncio
import hmac
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, Field

from verter.core.security import verify_password

if TYPE_CHECKING:
    from verter.dashboard.context import DashboardContext

SESSION_COOKIE = "verter_session"
_COOKIE_PATH = "/api"
_ALGORITHM = "HS256"
_cookie_scheme = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)

type SessionDependency = Callable[[str | None], Awaitable[SessionInfo]]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionInfo(BaseModel):
    username: str


class ErrorResponse(BaseModel):
    detail: str


class LoginRateLimiter:
    """Sliding-window counter of failed logins per client address."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_s: float = 60.0,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window_s = window_s
        self._max_keys = max_keys
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def retry_after(self, key: str) -> float:
        """Seconds until ``key`` may try again; 0 when allowed now."""
        failures = self._failures.get(key)
        if failures is None:
            return 0.0
        now = self._clock()
        while failures and now - failures[0] >= self._window_s:
            failures.popleft()
        if not failures:
            del self._failures[key]
            return 0.0
        if len(failures) < self._max_failures:
            return 0.0
        return self._window_s - (now - failures[0])

    def record_failure(self, key: str) -> None:
        if key not in self._failures and len(self._failures) >= self._max_keys:
            del self._failures[next(iter(self._failures))]
        self._failures.setdefault(key, deque()).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def issue_token(username: str, secret: str, ttl_s: int, *, now: float | None = None) -> str:
    issued = int(time.time() if now is None else now)
    claims = {"sub": username, "iat": issued, "exp": issued + ttl_s}
    # PyJWT's key union references `cryptography` types, which are not installed.
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)  # pyright: ignore[reportUnknownMemberType]


def read_session(token: str | None, *, username: str, secret: str) -> SessionInfo | None:
    """Validate a session token; returns None for missing, expired, or foreign tokens."""
    if not token:
        return None
    try:
        claims = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token, secret, algorithms=[_ALGORITHM], options={"require": ["sub", "iat", "exp"]}
        )
    except jwt.InvalidTokenError:
        return None
    if claims.get("sub") != username:
        return None
    return SessionInfo(username=username)


def make_session_dependency(ctx: "DashboardContext") -> SessionDependency:
    async def require_session(
        token: Annotated[str | None, Depends(_cookie_scheme)],
    ) -> SessionInfo:
        session = read_session(token, username=ctx.config.dashboard.username, secret=ctx.jwt_secret)
        if session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")
        return session

    return require_session


def build_auth_router(ctx: "DashboardContext", require_session: SessionDependency) -> APIRouter:
    router = APIRouter()
    errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    }

    @router.post("/login", responses=errors)
    async def login(body: LoginRequest, request: Request, response: Response) -> SessionInfo:
        settings = ctx.config.dashboard
        client = request.client.host if request.client else "unknown"
        wait = ctx.login_limiter.retry_after(client)
        if wait > 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too_many_attempts",
                headers={"Retry-After": str(math.ceil(wait))},
            )
        if not settings.password_hash:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "not_initialized")
        # The password is verified even for an unknown user so both paths take equal time.
        user_ok = hmac.compare_digest(body.username.encode(), settings.username.encode())
        password_ok = await asyncio.to_thread(
            verify_password, body.password, settings.password_hash
        )
        if not (user_ok and password_ok):
            ctx.login_limiter.record_failure(client)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
        ctx.login_limiter.reset(client)
        ttl_s = settings.session_ttl_hours * 3600
        response.set_cookie(
            SESSION_COOKIE,
            issue_token(settings.username, ctx.jwt_secret, ttl_s),
            max_age=ttl_s,
            path=_COOKIE_PATH,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return SessionInfo(username=settings.username)

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE,
            path=_COOKIE_PATH,
            secure=ctx.config.dashboard.cookie_secure,
            httponly=True,
            samesite="strict",
        )

    @router.get("/me", responses={401: {"model": ErrorResponse}})
    async def me(session: Annotated[SessionInfo, Depends(require_session)]) -> SessionInfo:
        return session

    return router
