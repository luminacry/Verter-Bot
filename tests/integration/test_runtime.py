import asyncio
from pathlib import Path

import pytest

from tests.helpers import ScriptedHandlers, reply
from verter.core.computer.runner import RunnerStatus
from verter.core.config import PlatformConfig, VerterConfig
from verter.core.core_lifecycle import CoreLifecycle, serve
from verter.core.message.message_event_result import MessageChain
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.scheduler import PipelineScheduler
from verter.core.pipeline.stage import registered_stages
from verter.core.platform.message_session import MessageSession
from verter.core.platform.platform import Platform
from verter.core.platform.platform_metadata import PlatformMetadata
from verter.core.platform.register import register_platform_adapter, unregister_platform_adapter
from verter.core.platform.sources.inmemory import InMemoryPlatform


async def test_lifecycle_routes_inmemory_messages(config: VerterConfig) -> None:
    config.dashboard.enable = False
    config.computer.enable = False
    config.platform_settings.rate_limit.enable = False
    lifecycle = CoreLifecycle(
        config,
        extra_platforms=[PlatformConfig(id="mem", type="inmemory")],
        handlers=ScriptedHandlers(commands={"hello": reply("hi")}),
    )
    await lifecycle.start()
    try:
        bus = lifecycle.event_bus
        assert bus is not None
        platform = lifecycle.platforms.get("mem") if lifecycle.platforms else None
        assert isinstance(platform, InMemoryPlatform)
        assert platform.inject("chatter", group_id="g") is not None
        await bus.wait_idle()
        assert platform.sent.empty()
        assert platform.inject("/hello") is not None
        sent = await platform.next_message()
        assert sent.chain.outline() == "hi"
        assert bus.stats.processed == 2
    finally:
        await lifecycle.stop()


async def test_available_sandbox_does_not_block_startup(
    config: VerterConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def start(self) -> RunnerStatus:
        return RunnerStatus(available=True, version="9.9.9", path=Path("/opt/verter-sandbox"))

    monkeypatch.setattr("verter.core.core_lifecycle.ComputerBooter.start", start)
    config.dashboard.enable = False
    lifecycle = CoreLifecycle(config)
    await lifecycle.start()
    await lifecycle.stop()


async def test_scheduler_reports_a_missing_stage(config: VerterConfig) -> None:
    removed = registered_stages.pop("PreProcessStage")
    try:
        scheduler = PipelineScheduler(PipelineContext(config))
        with pytest.raises(RuntimeError, match="PreProcessStage"):
            await scheduler.initialize()
    finally:
        registered_stages["PreProcessStage"] = removed


async def test_serve_stops_when_an_adapter_requests_shutdown(config: VerterConfig) -> None:
    class Quit(Platform):
        def meta(self) -> PlatformMetadata:
            return PlatformMetadata("quit", self.config.id, "requests shutdown")

        async def run(self) -> None:
            self.context.request_shutdown()

        async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
            return None

    register_platform_adapter("quit")(Quit)
    config.dashboard.enable = False
    config.computer.enable = False
    try:
        await asyncio.wait_for(
            serve(config, extra_platforms=[PlatformConfig(id="q", type="quit")]),
            2,
        )
    finally:
        unregister_platform_adapter("quit")
