"""Process lifecycle: start long-running components, then stop them in reverse order.

P0 owns only the dashboard server; the event bus, pipeline, and platform adapters
join this lifecycle in P1 and P2.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
import sys
from collections.abc import Generator
from pathlib import Path

import uvicorn

from verter.core.computer.runner import RunnerStatus, probe_runner, resolve_runner_binary
from verter.core.config import VerterConfig
from verter.core.log import logger
from verter.dashboard.app import create_app
from verter.dashboard.context import DashboardContext

_STARTUP_TIMEOUT_S = 10.0
_SHUTDOWN_TIMEOUT_S = 10.0


class _EmbeddedServer(uvicorn.Server):
    """uvicorn server that leaves signal handling to the lifecycle owner."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


def bind_tcp(host: str, port: int) -> socket.socket:
    """Bind a listening TCP socket so address errors surface as ``OSError`` here."""
    family, kind, proto, _, address = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(family, kind, proto)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
    except OSError:
        sock.close()
        raise
    return sock


class CoreLifecycle:
    def __init__(self, config: VerterConfig, *, webui_dist: Path | None = None) -> None:
        self.config = config
        self._webui_dist = webui_dist
        self._server: _EmbeddedServer | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._port: int | None = None

    @property
    def dashboard_url(self) -> str | None:
        if self._port is None:
            return None
        host = self.config.dashboard.host
        return f"http://[{host}]:{self._port}" if ":" in host else f"http://{host}:{self._port}"

    async def start(self) -> None:
        """Start all components.

        Raises:
            OSError: If the dashboard address cannot be bound.
            RuntimeError: If the dashboard server exits during startup.
        """
        runner = await self._probe_sandbox()
        settings = self.config.dashboard
        if not settings.enable:
            logger.info("Dashboard disabled in config")
            return
        app = create_app(DashboardContext.create(self.config, runner), webui_dist=self._webui_dist)
        sock = bind_tcp(settings.host, settings.port)
        self._port = sock.getsockname()[1]
        server = _EmbeddedServer(
            uvicorn.Config(
                app,
                log_config=None,
                access_log=False,
                lifespan="off",
                ws="none",
                server_header=False,
                timeout_graceful_shutdown=5,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]), name="verter-dashboard")
        async with asyncio.timeout(_STARTUP_TIMEOUT_S):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("dashboard server exited during startup")
                await asyncio.sleep(0.01)
        self._server, self._serve_task = server, task
        if self._webui_dist is None:
            logger.info("WebUI assets not found; serving the API only")
        logger.info("Dashboard listening on %s", self.dashboard_url)

    async def stop(self) -> None:
        server, task = self._server, self._serve_task
        self._server = self._serve_task = None
        if server is None or task is None:
            return
        server.should_exit = True
        try:
            await asyncio.wait_for(task, _SHUTDOWN_TIMEOUT_S)
        except TimeoutError:
            logger.warning("Dashboard did not stop within %.0fs", _SHUTDOWN_TIMEOUT_S)
        logger.info("Dashboard stopped")

    async def _probe_sandbox(self) -> RunnerStatus:
        policy = self.config.computer
        if not policy.enable:
            return RunnerStatus(available=False, error="sandbox disabled in config")
        status = await probe_runner(resolve_runner_binary())
        if status.available:
            logger.info(
                "Sandbox runner %s ready (%s, allow=%s, network=%s)",
                status.version,
                status.path,
                policy.allow,
                policy.network,
            )
        else:
            logger.warning("Sandbox runner unavailable: %s", status.error)
        return status


async def serve(
    config: VerterConfig, *, webui_dist: Path | None = None, stop: asyncio.Event | None = None
) -> None:
    """Run the lifecycle until ``stop`` is set, SIGTERM arrives, or the task is cancelled."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, stop.set)
    lifecycle = CoreLifecycle(config, webui_dist=webui_dist)
    try:
        await lifecycle.start()
        await stop.wait()
    finally:
        await lifecycle.stop()
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGTERM)
