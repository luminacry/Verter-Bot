import asyncio
from collections.abc import Callable

import pytest
from pydantic import BaseModel, ConfigDict

from verter.core.config import ConfigError, PlatformConfig
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.manager import PlatformManager
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.message_session import MessageSession
from verter.core.platform.platform import Platform, PlatformContext, PlatformStatus
from verter.core.platform.platform_metadata import PlatformMetadata
from verter.core.platform.register import (
    get_platform_adapter,
    register_platform_adapter,
    unregister_platform_adapter,
)


class _Sink:
    def commit(self, event: MessageEvent) -> bool:
        return True


def _manager(configs: list[PlatformConfig], **kwargs: float) -> PlatformManager:
    return PlatformManager(configs, _Sink(), request_shutdown=lambda: None, **kwargs)


class _Scripted(Platform):
    def meta(self) -> PlatformMetadata:
        return PlatformMetadata("scripted", self.config.id, "test adapter")

    async def run(self) -> None:
        return None

    async def send_by_session(self, session: MessageSession, message: MessageChain) -> None:
        return None


def _register(name: str, cls: type[Platform]) -> None:
    register_platform_adapter(name)(cls)


async def _until(predicate: Callable[[], bool]) -> None:
    while not predicate():  # noqa: ASYNC110 - another task publishes the status
        await asyncio.sleep(0.01)


async def test_unknown_and_duplicate_platforms_are_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown platform"):
        await _manager([PlatformConfig(id="x", type="missing")]).start()

    _register("scripted", _Scripted)
    try:
        configs = [
            PlatformConfig(id="same", type="scripted"),
            PlatformConfig(id="same", type="scripted"),
        ]
        with pytest.raises(ConfigError, match="duplicate platform"):
            await _manager(configs).start()
    finally:
        unregister_platform_adapter("scripted")


async def test_disabled_platforms_are_skipped_and_clean_exit_stays_stopped() -> None:
    _register("once", _Scripted)
    try:
        manager = _manager(
            [
                PlatformConfig(id="off", type="once", enable=False),
                PlatformConfig(id="on", type="once"),
            ]
        )
        await manager.start()
        platform = manager.get("on")
        assert platform is not None
        await asyncio.wait_for(_until(lambda: platform.status is PlatformStatus.STOPPED), 1)
        assert manager.get("off") is None
        stat = manager.stats()[0]
        assert (stat.id, stat.restarts, stat.status) == ("on", 0, PlatformStatus.STOPPED)
        await manager.stop()
    finally:
        unregister_platform_adapter("once")


async def test_crashed_platforms_restart_with_backoff() -> None:
    class Flaky(_Scripted):
        def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
            super().__init__(config, context)
            self.runs = 0
            self.stable = asyncio.Event()

        async def run(self) -> None:
            self.runs += 1
            if self.runs < 3:
                raise RuntimeError(f"boom {self.runs}")
            self.stable.set()
            await asyncio.Event().wait()

        async def terminate(self) -> None:
            return None

    _register("flaky", Flaky)
    try:
        manager = _manager(
            [PlatformConfig(id="f", type="flaky")], restart_initial_s=0.01, restart_max_s=0.05
        )
        await manager.start()
        platform = manager.get("f")
        assert isinstance(platform, Flaky)
        await asyncio.wait_for(platform.stable.wait(), 1)
        assert platform.runs == 3
        stat = manager.stats()[0]
        assert stat.restarts == 2
        assert stat.last_error is not None
        assert "boom" in stat.last_error
        assert stat.next_restart_at is None
        await manager.stop()
        assert platform.status is PlatformStatus.STOPPED
    finally:
        unregister_platform_adapter("flaky")


async def test_manual_restart_during_backoff_resets_the_delay() -> None:
    class Broken(_Scripted):
        async def run(self) -> None:
            raise RuntimeError("always")

        async def terminate(self) -> None:
            return None

    _register("broken", Broken)
    try:
        manager = _manager(
            [PlatformConfig(id="b", type="broken")], restart_initial_s=30, restart_max_s=30
        )
        await manager.start()
        await asyncio.wait_for(
            _until(lambda: manager.stats()[0].status is PlatformStatus.BACKOFF), 1
        )
        before = manager.stats()[0].restarts
        await manager.restart("b")
        # The counter is kept across a manual restart; the fresh run crashes back into backoff.
        await asyncio.wait_for(
            _until(lambda: manager.stats()[0].status is PlatformStatus.BACKOFF), 1
        )
        assert manager.stats()[0].restarts == before
        await manager.stop()
    finally:
        unregister_platform_adapter("broken")


async def test_concurrent_restart_is_rejected_and_others_are_untouched() -> None:
    class Slow(_Scripted):
        def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
            super().__init__(config, context)
            self.started = asyncio.Event()
            self.run_count = 0

        async def run(self) -> None:
            self.run_count += 1
            self.started.set()
            await asyncio.Event().wait()

        async def terminate(self) -> None:
            await asyncio.sleep(0.05)

    _register("slow", Slow)
    try:
        manager = _manager(
            [PlatformConfig(id="a", type="slow"), PlatformConfig(id="b", type="slow")]
        )
        await manager.start()
        first = manager.get("a")
        other = manager.get("b")
        assert isinstance(first, Slow)
        assert isinstance(other, Slow)
        await asyncio.wait_for(first.started.wait(), 1)
        # restart() is called while run() is still in flight (not crashed/backing off).
        restarting = asyncio.ensure_future(manager.restart("a"))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="restart already in progress"):
            await manager.restart("a")
        await restarting
        # The adapter actually came back up: run() was invoked a second time and the
        # manager reports it running again, not stuck in some half-restarted state.
        assert first.run_count == 2
        assert manager.stats()[0].status is PlatformStatus.RUNNING
        assert other.started.is_set()
        with pytest.raises(KeyError):
            await manager.restart("missing")
        await manager.stop()
        with pytest.raises(RuntimeError, match="manager is stopping"):
            await manager.restart("a")
    finally:
        unregister_platform_adapter("slow")


async def test_terminate_errors_do_not_block_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stuck(_Scripted):
        def __init__(self, config: PlatformConfig, context: PlatformContext) -> None:
            super().__init__(config, context)
            self.running = asyncio.Event()

        async def run(self) -> None:
            self.running.set()
            await asyncio.Event().wait()

        async def terminate(self) -> None:
            await asyncio.sleep(30)

    monkeypatch.setattr("verter.core.platform.manager._TERMINATE_TIMEOUT_S", 0.05)
    _register("stuck", Stuck)
    try:
        manager = _manager([PlatformConfig(id="s", type="stuck")])
        await manager.start()
        platform = manager.get("s")
        assert isinstance(platform, Stuck)
        await platform.running.wait()
        await asyncio.wait_for(manager.stop(), 1)
        assert manager.platforms == []
    finally:
        unregister_platform_adapter("stuck")


def test_builtin_adapters_resolve_lazily() -> None:
    assert get_platform_adapter("inmemory").__name__ == "InMemoryPlatform"
    assert get_platform_adapter("console").__name__ == "ConsolePlatform"


def test_registering_the_same_adapter_twice_is_idempotent() -> None:
    _register("scripted", _Scripted)
    try:
        _register("scripted", _Scripted)
        assert get_platform_adapter("scripted") is _Scripted
        with pytest.raises(ValueError, match="already registered"):

            @register_platform_adapter("scripted")
            class _Other(_Scripted):
                pass
    finally:
        unregister_platform_adapter("scripted")


class _TokenSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = ""
    port: int = 0


def _scripted(**settings: object) -> _Scripted:
    config = PlatformConfig.model_validate({"id": "p", "type": "scripted", **settings})
    return _Scripted(config, PlatformContext(sink=_Sink(), request_shutdown=lambda: None))


def test_settings_read_env_references(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERTER_TEST_TOKEN", "s3cret")
    parsed = _scripted(token="ENV:VERTER_TEST_TOKEN", port=6199).parse_settings(_TokenSettings)
    assert parsed.token == "s3cret"
    assert parsed.port == 6199
    # Strings without the prefix are left alone.
    assert _scripted(token="plain").parse_settings(_TokenSettings).token == "plain"


def test_missing_or_empty_env_references_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERTER_TEST_MISSING", raising=False)
    with pytest.raises(ConfigError, match=r"VERTER_TEST_MISSING.*not set"):
        _scripted(token="ENV:VERTER_TEST_MISSING").parse_settings(_TokenSettings)
    with pytest.raises(ConfigError, match="empty environment variable name"):
        _scripted(token="ENV:").parse_settings(_TokenSettings)
