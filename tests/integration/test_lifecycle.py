import asyncio

import httpx
import pytest

from verter.core.config import VerterConfig
from verter.core.core_lifecycle import CoreLifecycle, bind_tcp, serve


async def test_dashboard_serves_on_an_ephemeral_port(config: VerterConfig) -> None:
    config.dashboard.port = 0
    config.computer.enable = False
    lifecycle = CoreLifecycle(config)
    await lifecycle.start()
    try:
        url = lifecycle.dashboard_url
        assert url is not None
        assert url.startswith("http://127.0.0.1:")
        async with httpx.AsyncClient() as http:
            response = await http.get(f"{url}/api/health")
        assert response.json() == {"status": "ok"}
    finally:
        await lifecycle.stop()
    await lifecycle.stop()


async def test_start_fails_when_the_port_is_taken(config: VerterConfig) -> None:
    holder = bind_tcp("127.0.0.1", 0)
    holder.listen()
    try:
        config.dashboard.port = holder.getsockname()[1]
        config.computer.enable = False
        with pytest.raises(OSError):  # noqa: PT011 - errno differs between platforms
            await CoreLifecycle(config).start()
    finally:
        holder.close()


def test_ipv6_urls_are_bracketed(config: VerterConfig) -> None:
    config.dashboard.host = "::1"
    lifecycle = CoreLifecycle(config)
    lifecycle._port = 6185  # pyright: ignore[reportPrivateUsage]
    assert lifecycle.dashboard_url == "http://[::1]:6185"


@pytest.mark.parametrize("dashboard_enabled", [True, False])
async def test_serve_runs_until_stopped(config: VerterConfig, dashboard_enabled: bool) -> None:
    config.dashboard.enable = dashboard_enabled
    config.dashboard.port = 0
    config.computer.enable = False
    stop = asyncio.Event()
    task = asyncio.create_task(serve(config, stop=stop))
    await asyncio.sleep(0.2)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 10)


async def test_sandbox_probe_runs_when_enabled(
    config: VerterConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERTER_SANDBOX_BIN", "/nonexistent/verter-sandbox")
    config.dashboard.enable = False
    lifecycle = CoreLifecycle(config)
    await lifecycle.start()
    assert lifecycle.dashboard_url is None
    await lifecycle.stop()
