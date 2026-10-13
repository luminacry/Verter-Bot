import hashlib
import sys
from pathlib import Path

import pytest

from verter.core.computer import runner as runner_module
from verter.core.computer.booter import ComputerBooter, SandboxDeniedError
from verter.core.computer.runner import (
    ENV_RUNNER_BIN,
    PROTOCOL_VERSION,
    RunnerError,
    SandboxClient,
    probe_runner,
    resolve_runner_binary,
)
from verter.core.config import ComputerConfig

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


async def test_handshake_and_exec(runner_binary: Path, tmp_path: Path) -> None:
    command = ["cmd", "/c", "echo ok"] if sys.platform == "win32" else ["/usr/bin/echo", "ok"]
    async with SandboxClient(runner_binary, timeout=30) as client:
        ack = await client.start()
        assert ack.protocol_version == PROTOCOL_VERSION
        result = await client.exec(
            command,
            workspace=tmp_path,
            timeout_ms=15_000,
            memory_bytes=256 * 1024 * 1024,
            nproc=64,
        )
    assert result.exit_code == 0
    assert not result.timed_out
    assert b"ok" in result.stdout
    if sys.platform == "win32":
        assert result.backend in {"appcontainer", "isolated"}
    else:
        assert result.backend in {"bubblewrap", "landlock"}


async def test_probe_reports_the_runner(runner_binary: Path) -> None:
    status = await probe_runner(runner_binary)
    assert status.available
    assert status.path == runner_binary
    assert status.error is None
    if sys.platform.startswith("linux"):
        assert [b.kind for b in status.backends] == ["bubblewrap", "landlock"]


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


async def test_booter_denies_unauthorized_sessions(tmp_path: Path) -> None:
    booter = ComputerBooter(ComputerConfig(allow="admins"), tmp_path)
    with pytest.raises(SandboxDeniedError, match="not allowed"):
        await booter.exec(["/usr/bin/echo", "no"], session_id="qq:GroupMessage:1", is_admin=False)
    assert not (tmp_path / "workspaces").exists()


async def test_booter_starts_and_executes(runner_binary: Path, tmp_path: Path) -> None:
    command = ["cmd", "/c", "echo ok"] if sys.platform == "win32" else ["/usr/bin/echo", "ok"]
    booter = ComputerBooter(ComputerConfig(allow="all"), tmp_path, binary=runner_binary)
    status = await booter.start()
    try:
        assert status.available
        assert status.version
        assert booter.status.available
        result = await booter.exec(command, session_id="s1", is_admin=False)
        assert result.exit_code == 0
        assert b"ok" in result.stdout
        digest = hashlib.sha256(b"s1").hexdigest()[:16]
        assert (tmp_path / "workspaces" / digest).is_dir()
    finally:
        await booter.stop()
    assert not booter.status.available


async def test_exec_invalid_workspace_is_an_error(runner_binary: Path, tmp_path: Path) -> None:
    command = ["cmd", "/c", "echo ok"] if sys.platform == "win32" else ["/usr/bin/echo", "ok"]
    async with SandboxClient(runner_binary, timeout=30) as client:
        await client.start()
        with pytest.raises(RunnerError, match="invalid_workspace"):
            await client.exec(command, workspace=tmp_path / "missing")


async def test_wrong_platform_backend_is_an_error(runner_binary: Path, tmp_path: Path) -> None:
    command = ["cmd", "/c", "echo ok"] if sys.platform == "win32" else ["/usr/bin/echo", "ok"]
    backend = "bubblewrap" if sys.platform == "win32" else "appcontainer"
    async with SandboxClient(runner_binary, timeout=30) as client:
        await client.start()
        with pytest.raises(RunnerError, match="invalid_exec"):
            await client.exec(command, workspace=tmp_path, backend=backend)


async def test_exec_output_is_truncated(runner_binary: Path, tmp_path: Path) -> None:
    payload = "x" * 64
    if sys.platform == "win32":
        command = ["cmd", "/c", f"echo {payload}"]
    else:
        command = ["/usr/bin/printf", "%s", payload]
    async with SandboxClient(runner_binary, timeout=30) as client:
        await client.start()
        result = await client.exec(command, workspace=tmp_path, output_limit=8)
    assert result.truncated
    assert len(result.stdout) == 8


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "verter-sandbox"
    binary.write_bytes(b"")
    monkeypatch.setenv(ENV_RUNNER_BIN, str(binary))
    assert resolve_runner_binary() == binary
    monkeypatch.setenv(ENV_RUNNER_BIN, str(tmp_path / "absent"))
    assert resolve_runner_binary() is None
