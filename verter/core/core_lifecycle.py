"""Process lifecycle: start long-running components, then stop them in reverse order.

Start order: pipeline, event bus, platform adapters, dashboard. Stopping first
halts the adapters so no new events arrive, then drains the event bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import signal
import socket
import sys
from collections.abc import Callable, Generator, Sequence
from pathlib import Path

import uvicorn

from verter.core.computer.runner import RunnerStatus, probe_runner, resolve_runner_binary
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.db import Database
from verter.core.event_bus import EventBus
from verter.core.log import logger
from verter.core.pipeline.context import HandlerSource, NullHandlerSource, PipelineContext
from verter.core.pipeline.scheduler import PipelineScheduler
from verter.core.platform.manager import PlatformManager
from verter.core.runtime_state import RuntimeHandle, current_runtime
from verter.core.star.plugin_manager import PluginManager
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


def _ignore_shutdown_request() -> None:
    return None


class CoreLifecycle:
    def __init__(
        self,
        config: VerterConfig,
        *,
        webui_dist: Path | None = None,
        extra_platforms: Sequence[PlatformConfig] = (),
        handlers: HandlerSource | None = None,
        data_dir: Path | None = None,
        request_shutdown: Callable[[], None] = _ignore_shutdown_request,
    ) -> None:
        self.config = config
        self._webui_dist = webui_dist
        self._extra_platforms = list(extra_platforms)
        self._handlers = handlers
        self._data_dir = data_dir
        self._request_shutdown = request_shutdown
        self.pipeline: PipelineScheduler | None = None
        self.event_bus: EventBus | None = None
        self.platforms: PlatformManager | None = None
        self.plugins: PluginManager | None = None
        self._db: Database | None = None
        self._handle: RuntimeHandle | None = None
        self._runtime_token: contextvars.Token[RuntimeHandle | None] | None = None
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
        """Start all components; call ``stop()`` afterwards even if this raises.

        Raises:
            ConfigError: If a platform entry is invalid.
            OSError: If the dashboard address cannot be bound.
            RuntimeError: If the dashboard server exits during startup.
        """
        runner = await self._probe_sandbox()
        await self._start_runtime()
        await self._start_dashboard(runner)

    async def stop(self) -> None:
        platforms, self.platforms = self.platforms, None
        if platforms is not None:
            await platforms.stop()
        bus, self.event_bus = self.event_bus, None
        if bus is not None:
            await bus.stop()
        plugins, self.plugins = self.plugins, None
        if plugins is not None:
            await plugins.close()
        db, self._db = self._db, None
        if db is not None:
            await db.close()
        token, self._runtime_token = self._runtime_token, None
        if token is not None:
            current_runtime.reset(token)
        await self._stop_dashboard()

    async def _start_runtime(self) -> None:
        source = await self._handler_source()
        self._handle = RuntimeHandle()
        self._runtime_token = current_runtime.set(self._handle)
        pipeline = PipelineScheduler(PipelineContext(self.config, source))
        await pipeline.initialize()
        self.pipeline = pipeline
        runtime = self.config.runtime
        bus = EventBus(
            pipeline,
            max_pending=runtime.max_pending_events,
            workers=runtime.workers,
            event_timeout_s=runtime.event_timeout_s,
        )
        await bus.start()
        self.event_bus = bus
        self.platforms = PlatformManager(
            [*self.config.platform, *self._extra_platforms],
            bus,
            request_shutdown=self._request_shutdown,
        )
        await self.platforms.start()
        self._handle.platforms = self.platforms

    async def _handler_source(self) -> HandlerSource:
        if self._handlers is not None:
            return self._handlers
        if self._data_dir is None:
            return NullHandlerSource()
        db = Database(self._data_dir / "verter.db")
        await db.connect()
        self._db = db
        plugins = PluginManager(self.config, db, self._data_dir)
        await plugins.load()
        self.plugins = plugins
        return plugins

    async def _start_dashboard(self, runner: RunnerStatus) -> None:
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

    async def _stop_dashboard(self) -> None:
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
    config: VerterConfig,
    *,
    webui_dist: Path | None = None,
    extra_platforms: Sequence[PlatformConfig] = (),
    data_dir: Path | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Run until ``stop`` is set, SIGTERM arrives, an adapter requests shutdown, or cancellation."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, stop.set)
    lifecycle = CoreLifecycle(
        config,
        webui_dist=webui_dist,
        extra_platforms=extra_platforms,
        data_dir=data_dir,
        request_shutdown=stop.set,
    )
    try:
        await lifecycle.start()
        await stop.wait()
    finally:
        await lifecycle.stop()
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGTERM)
