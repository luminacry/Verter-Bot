import sys
from pathlib import Path

import pytest

from verter.core.computer import runner as runner_module
from verter.core.computer.runner import (
    ENV_RUNNER_BIN,
    PROTOCOL_VERSION,
    RunnerError,
    SandboxClient,
    probe_runner,
    resolve_runner_binary,
)

RUST_OS = {"linux": "linux", "win32": "windows", "darwin": "macos"}[sys.platform]


@pytest.fixture
def runner_binary() -> Path:
    path = resolve_runner_binary()
    if path is None:
        pytest.skip("verter-sandbox is not built; run `just build-sandbox`")
    return path


async def test_handshake_and_ping(runner_binary: Path) -> None:
    async with SandboxClient(runner_binary) as client:
        ack = await client.start()
        assert ack.protocol_version == PROTOCOL_VERSION
        assert ack.server_version
        assert ack.platform.os == RUST_OS
        await client.ping(2**40)
        await client.ping(0)


async def test_probe_reports_the_runner(runner_binary: Path) -> None:
    status = await probe_runner(runner_binary)
    assert status.available
    assert status.path == runner_binary
    assert status.error is None
    if sys.platform.startswith("linux"):
        assert [b.kind for b in status.backends] == ["bubblewrap"]


async def test_protocol_mismatch_is_reported(
    runner_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "PROTOCOL_VERSION", PROTOCOL_VERSION + 1)
    status = await probe_runner(runner_binary)
    assert not status.available
    assert status.error is not None
    assert "unsupported_protocol_version" in status.error


async def test_probe_without_binary() -> None:
    status = await probe_runner(None)
    assert not status.available
    assert status.error == "verter-sandbox not found"


async def test_non_runner_executable_fails_the_handshake() -> None:
    client = SandboxClient(Path(sys.executable), timeout=10)
    with pytest.raises(RunnerError, match="runner exited"):
        await client.start()
    await client.close()


async def test_missing_executable_cannot_start(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="cannot start"):
        await SandboxClient(tmp_path / "missing").start()


async def test_requests_need_a_running_process(tmp_path: Path) -> None:
    client = SandboxClient(tmp_path / "unused")
    with pytest.raises(RunnerError, match="not running"):
        await client.ping(1)
    await client.close()


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "verter-sandbox"
    binary.write_bytes(b"")
    monkeypatch.setenv(ENV_RUNNER_BIN, str(binary))
    assert resolve_runner_binary() == binary
    monkeypatch.setenv(ENV_RUNNER_BIN, str(tmp_path / "absent"))
    assert resolve_runner_binary() is None
