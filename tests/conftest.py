import logging
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from verter.core.computer.protocol import BackendStatus
from verter.core.computer.runner import RunnerStatus
from verter.core.config import VerterConfig
from verter.core.security import hash_password
from verter.dashboard.app import create_app
from verter.dashboard.context import DashboardContext

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def admin_password() -> str:
    return PASSWORD


@pytest.fixture(scope="session")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def config(password_hash: str) -> VerterConfig:
    cfg = VerterConfig()
    cfg.dashboard.password_hash = password_hash
    cfg.dashboard.jwt_secret = "test-secret-" + "x" * 32
    return cfg


@pytest.fixture
def runner_status() -> RunnerStatus:
    return RunnerStatus(
        available=True,
        version="0.1.0",
        backends=(BackendStatus(kind="bubblewrap", available=True, detail="/usr/bin/bwrap"),),
    )


@pytest.fixture
def ctx(config: VerterConfig, runner_status: RunnerStatus) -> DashboardContext:
    return DashboardContext.create(config, runner_status)


@pytest.fixture
async def client(ctx: DashboardContext) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app(ctx))
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
