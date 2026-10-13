"""Dashboard metrics, logs, and plugin administration."""

from pathlib import Path

from httpx import ASGITransport, AsyncClient

from verter.core.config import VerterConfig
from verter.core.db import Database
from verter.core.log_ring import LogRing
from verter.core.star.plugin_manager import PluginManager
from verter.dashboard.app import create_app
from verter.dashboard.context import DashboardContext
from verter.dashboard.runtime import _stream


async def _plugins_client(
    tmp_path: Path, config: VerterConfig, ctx: DashboardContext
) -> tuple[AsyncClient, PluginManager, Database]:
    broken = tmp_path / "plugins" / "broken"
    broken.mkdir(parents=True)
    (broken / "main.py").write_text("raise RuntimeError('import boom')\n", encoding="utf-8")
    db = Database(tmp_path / "verter.db")
    await db.connect()
    plugins = PluginManager(config, db, tmp_path)
    await plugins.load()
    ctx.plugins = plugins
    ctx.logs = LogRing()
    transport = ASGITransport(app=create_app(ctx))
    client = AsyncClient(transport=transport, base_url="http://testserver")
    return client, plugins, db


async def test_metrics_logs_and_plugins_require_a_session(client: AsyncClient) -> None:
    for path in (
        "/api/metrics",
        "/api/metrics/stream",
        "/api/logs",
        "/api/plugins",
        "/api/sandbox/doctor",
        "/api/sandbox/audit",
    ):
        response = await client.get(path)
        assert response.status_code == 401


async def test_metrics_and_logs_when_nothing_is_running(
    client: AsyncClient, admin_password: str
) -> None:
    assert (
        await client.post("/api/auth/login", json={"username": "admin", "password": admin_password})
    ).status_code == 200
    metrics = await client.get("/api/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["received"] == 0
    assert body["pending"] == 0
    assert body["latency"]["total"] == 0
    assert (await client.get("/api/plugins")).json() == []


class _DisconnectAfter:
    def __init__(self) -> None:
        self._checks = 0

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > 1


async def test_metrics_stream_emits_one_snapshot_then_stops(ctx: DashboardContext) -> None:
    chunks = [chunk async for chunk in _stream(ctx, _DisconnectAfter(), interval_s=0)]
    assert len(chunks) == 1
    assert chunks[0].startswith("data: ")
    assert '"dropped": 0' in chunks[0]
    assert chunks[0].endswith("\n\n")


async def test_plugin_actions_round_trip(
    tmp_path: Path, config: VerterConfig, ctx: DashboardContext, admin_password: str
) -> None:
    client, plugins, db = await _plugins_client(tmp_path, config, ctx)
    try:
        async with client:
            logged_in = await client.post(
                "/api/auth/login", json={"username": "admin", "password": admin_password}
            )
            assert logged_in.status_code == 200
            listing = (await client.get("/api/plugins")).json()
            names = {item["name"]: item for item in listing}
            assert names["helloworld"]["enabled"] is True
            assert names["builtin_commands"]["reserved"] is True
            assert "import boom" in names["broken"]["error"]

            refused = await client.post(
                "/api/plugins/builtin_commands/enabled", json={"enabled": False}
            )
            assert refused.status_code == 200
            assert refused.json()["ok"] is False
            assert "不能关闭内置指令" in refused.json()["message"]

            turned_off = await client.post(
                "/api/plugins/helloworld/enabled", json={"enabled": False}
            )
            assert turned_off.json() == {"ok": True, "message": "已关闭 helloworld"}
            reloaded = await client.post("/api/plugins/helloworld/reload")
            assert reloaded.json()["ok"] is True
            assert reloaded.json()["message"] == "已重载 helloworld"

            missing = await client.post("/api/plugins/nope/reload")
            assert missing.json()["ok"] is False
            bad_name = await client.post("/api/plugins/a.b/reload")
            assert bad_name.status_code == 404
    finally:
        await plugins.close()
        await db.close()


async def test_logs_endpoint_returns_the_ring(
    client: AsyncClient, ctx: DashboardContext, admin_password: str
) -> None:
    import logging

    ring = LogRing()
    ctx.logs = ring
    logger = logging.getLogger("verter.test.api")
    ring.append(logger.makeRecord(logger.name, logging.WARNING, __file__, 1, "kept", (), None))
    assert (
        await client.post("/api/auth/login", json={"username": "admin", "password": admin_password})
    ).status_code == 200
    response = await client.get("/api/logs", params={"limit": 10})
    assert response.status_code == 200
    assert response.json()[0]["message"] == "kept"
    assert response.json()[0]["level"] == "WARNING"


async def test_sandbox_audit_and_doctor(
    tmp_path: Path, ctx: DashboardContext, admin_password: str
) -> None:
    db = Database(tmp_path / "verter.db")
    await db.connect()
    ctx.db = db
    transport = ASGITransport(app=create_app(ctx))
    client = AsyncClient(transport=transport, base_url="http://testserver")
    try:
        async with client:
            assert (
                await client.post(
                    "/api/auth/login", json={"username": "admin", "password": admin_password}
                )
            ).status_code == 200
            await db.record_sandbox_audit(
                session_id="s",
                initiator="bob",
                command="echo hi",
                allowed=False,
                error="sandbox not allowed",
            )
            listing = (await client.get("/api/sandbox/audit")).json()
            assert listing[0]["initiator"] == "bob"
            assert listing[0]["allowed"] is False
            doctor = await client.get("/api/sandbox/doctor")
            assert doctor.status_code == 200
            body = doctor.json()
            assert body["auto"]
            assert body["policy"]
            assert "can_execute" in body
    finally:
        await db.close()
