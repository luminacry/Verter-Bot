import time

import pytest
from httpx import ASGITransport, AsyncClient

from verter import __version__
from verter.core.computer.runner import RunnerStatus
from verter.core.config import VerterConfig
from verter.dashboard.app import create_app
from verter.dashboard.auth import SESSION_COOKIE, issue_token
from verter.dashboard.context import DashboardContext


async def login(client: AsyncClient, password: str) -> int:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": password}
    )
    return response.status_code


async def test_health_is_public_and_hardened(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/system/info"])
async def test_protected_endpoints_require_a_session(client: AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


async def test_login_issues_strict_http_only_cookie(
    client: AsyncClient, admin_password: str
) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": admin_password}
    )
    assert response.status_code == 200
    assert response.json() == {"username": "admin"}
    cookie = response.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api" in cookie
    me = await client.get("/api/auth/me")
    assert me.json() == {"username": "admin"}


async def test_wrong_credentials_are_rejected(client: AsyncClient, admin_password: str) -> None:
    assert await login(client, "wrong") == 401
    response = await client.post(
        "/api/auth/login", json={"username": "root", "password": admin_password}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_credentials"}


async def test_login_body_is_validated(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"username": "", "password": "x"})
    assert response.status_code == 422


async def test_repeated_failures_are_throttled(client: AsyncClient, admin_password: str) -> None:
    for _ in range(5):
        assert await login(client, "wrong") == 401
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": admin_password}
    )
    assert response.status_code == 429
    assert response.json() == {"detail": "too_many_attempts"}
    assert 0 < int(response.headers["retry-after"]) <= 60


async def test_logout_clears_the_session(client: AsyncClient, admin_password: str) -> None:
    assert await login(client, admin_password) == 200
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        issue_token("admin", "another-secret-" + "y" * 32, 3600),
        issue_token("admin", "test-secret-" + "x" * 32, 60, now=time.time() - 3600),
        issue_token("intruder", "test-secret-" + "x" * 32, 3600),
    ],
    ids=["foreign-secret", "expired", "other-user"],
)
async def test_invalid_session_tokens_are_rejected(client: AsyncClient, token: str) -> None:
    response = await client.get("/api/auth/me", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert response.status_code == 401


async def test_system_info_reports_runtime_and_sandbox(
    client: AsyncClient, admin_password: str
) -> None:
    assert await login(client, admin_password) == 200
    response = await client.get("/api/system/info")
    assert response.status_code == 200
    info = response.json()
    assert info["version"] == __version__
    assert info["uptime_seconds"] >= 0
    assert info["os"] in {"linux", "windows", "darwin"}
    assert info["sandbox"]["policy"] == {
        "enabled": True,
        "backend": "local",
        "allow": "admins",
        "network": "deny",
    }
    assert info["sandbox"]["runner"] == {
        "available": True,
        "version": "0.1.0",
        "backends": [{"kind": "bubblewrap", "available": True, "detail": "/usr/bin/bwrap"}],
        "error": None,
    }


async def test_login_requires_initialized_credentials() -> None:
    config = VerterConfig()
    config.dashboard.jwt_secret = "s" * 43
    ctx = DashboardContext.create(config, RunnerStatus(available=False))
    transport = ASGITransport(app=create_app(ctx))
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert response.status_code == 503
    assert response.json() == {"detail": "not_initialized"}


def test_missing_secret_falls_back_to_ephemeral(config: VerterConfig) -> None:
    config.dashboard.jwt_secret = ""
    first = DashboardContext.create(config, RunnerStatus(available=False))
    second = DashboardContext.create(config, RunnerStatus(available=False))
    assert first.jwt_secret
    assert first.jwt_secret != second.jwt_secret
