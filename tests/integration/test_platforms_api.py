"""Platform status API: listing, restart, and error redaction."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from verter.core.config import PlatformConfig, VerterConfig
from verter.core.platform.manager import PlatformStat
from verter.core.platform.platform import PlatformStatus
from verter.dashboard.app import create_app
from verter.dashboard.context import DashboardContext


class _Adapter:
    def __init__(self, online: bool | None = None, heartbeat: float | None = None) -> None:
        self.online = online
        self.last_heartbeat = heartbeat


class _Manager:
    """Stands in for PlatformManager with a fixed view and a record of restarts."""

    def __init__(self, stats: list[PlatformStat], adapters: dict[str, object]) -> None:
        self._stats = stats
        self._adapters = adapters
        self.restarted: list[str] = []
        self.error: Exception | None = None

    def stats(self) -> list[PlatformStat]:
        return self._stats

    def get(self, platform_id: str) -> object | None:
        return self._adapters.get(platform_id)

    async def restart(self, platform_id: str) -> None:
        if self.error is not None:
            raise self.error
        if platform_id not in self._adapters:
            raise KeyError(platform_id)
        self.restarted.append(platform_id)


def _stat(pid: str, kind: str, status: PlatformStatus, error: str | None = None) -> PlatformStat:
    return PlatformStat(pid, kind, status, 0, error, None)


async def _client(ctx: DashboardContext) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app(ctx)), base_url="http://testserver")


async def test_platforms_require_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/platforms")).status_code == 401
    assert (await client.post("/api/platforms/x/restart")).status_code == 401


async def test_platform_list_reports_status_and_presence(
    client: AsyncClient, admin_password: str, ctx: DashboardContext
) -> None:
    loop = asyncio.get_running_loop()
    ctx.platforms = _Manager(  # type: ignore[assignment]
        [
            _stat("qq", "onebot_v11", PlatformStatus.RUNNING),
            _stat("tg", "telegram", PlatformStatus.BACKOFF),
            _stat("dev", "console", PlatformStatus.RUNNING),
        ],
        {
            "qq": _Adapter(online=True, heartbeat=loop.time() - 5),
            "tg": _Adapter(online=False),
            "dev": _Adapter(),
        },
    )
    assert (
        await client.post("/api/auth/login", json={"username": "admin", "password": admin_password})
    ).status_code == 200
    body = (await client.get("/api/platforms")).json()
    by_id = {item["id"]: item for item in body}
    assert by_id["qq"]["online"] is True
    assert by_id["qq"]["last_seen_ago_s"] >= 5
    assert by_id["tg"]["status"] == "backoff"
    assert by_id["tg"]["online"] is False
    assert by_id["dev"]["online"] is None


async def test_last_error_hides_tokens(
    client: AsyncClient, admin_password: str, ctx: DashboardContext, config: VerterConfig
) -> None:
    config.platform = [
        PlatformConfig.model_validate(
            {"id": "qq", "type": "onebot_v11", "access_token": "super-secret"}
        )
    ]
    ctx.platforms = _Manager(  # type: ignore[assignment]
        [
            _stat(
                "qq",
                "onebot_v11",
                PlatformStatus.BACKOFF,
                "failed for super-secret at /bot123:ABCDEF/x",
            )
        ],
        {"qq": _Adapter()},
    )
    await client.post("/api/auth/login", json={"username": "admin", "password": admin_password})
    body = (await client.get("/api/platforms")).json()
    assert "super-secret" not in body[0]["last_error"]
    assert "123:ABCDEF" not in body[0]["last_error"]
    assert "<redacted>" in body[0]["last_error"]


async def test_env_configured_token_is_redacted(
    client: AsyncClient,
    admin_password: str,
    ctx: DashboardContext,
    config: VerterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QQ_TOKEN", "env-secret-value")
    config.platform = [
        PlatformConfig.model_validate(
            {"id": "qq", "type": "onebot_v11", "access_token": "ENV:QQ_TOKEN"}
        )
    ]
    ctx.platforms = _Manager(  # type: ignore[assignment]
        [
            _stat(
                "qq",
                "onebot_v11",
                PlatformStatus.BACKOFF,
                "connect ws://h/x?access_token=env-secret-value failed",
            )
        ],
        {"qq": _Adapter()},
    )
    await client.post("/api/auth/login", json={"username": "admin", "password": admin_password})
    error = (await client.get("/api/platforms")).json()[0]["last_error"]
    assert "env-secret-value" not in error
    assert "access_token=<redacted>" in error


async def test_restart_outcomes(
    client: AsyncClient, admin_password: str, ctx: DashboardContext
) -> None:
    manager = _Manager(
        [_stat("qq", "onebot_v11", PlatformStatus.RUNNING)], {"qq": _Adapter(online=True)}
    )
    ctx.platforms = manager  # type: ignore[assignment]
    await client.post("/api/auth/login", json={"username": "admin", "password": admin_password})

    restarted = await client.post("/api/platforms/qq/restart")
    assert restarted.status_code == 200
    assert manager.restarted == ["qq"]

    assert (await client.post("/api/platforms/missing/restart")).status_code == 404

    manager.error = RuntimeError("restart already in progress")
    assert (await client.post("/api/platforms/qq/restart")).status_code == 409

    manager.error = RuntimeError("manager is stopping")
    assert (await client.post("/api/platforms/qq/restart")).status_code == 503

    ctx.platforms = None
    assert (await client.post("/api/platforms/qq/restart")).status_code == 503
