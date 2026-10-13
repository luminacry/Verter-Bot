import hashlib
from pathlib import Path

import pytest

from verter.core.computer.booter import ComputerBooter, SandboxDeniedError
from verter.core.computer.runner import RunnerError
from verter.core.config import ComputerConfig
from verter.core.db import Database


def test_workspace_uses_session_hash(tmp_path: Path) -> None:
    booter = ComputerBooter(ComputerConfig(), tmp_path)
    path = booter.workspace("qq:GroupMessage:1")
    digest = hashlib.sha256(b"qq:GroupMessage:1").hexdigest()[:16]
    assert path == tmp_path / "workspaces" / digest
    assert path.is_dir()


async def test_allows_denial_does_not_need_a_runner(tmp_path: Path) -> None:
    policy = ComputerConfig(allow="whitelist", whitelist=["ok:session"])
    db = Database(tmp_path / "verter.db")
    await db.connect()
    booter = ComputerBooter(policy, tmp_path)
    booter.bind_database(db)
    try:
        with pytest.raises(SandboxDeniedError, match="not allowed"):
            await booter.exec(
                ["echo", "no"],
                session_id="other:session",
                is_admin=False,
                initiator="eve",
            )
        assert not (tmp_path / "workspaces").exists()
        rows = await db.list_sandbox_audit()
        assert len(rows) == 1
        assert rows[0].allowed is False
        assert rows[0].initiator == "eve"
        assert rows[0].command == "echo no"
    finally:
        await db.close()


async def test_exec_without_start_is_an_error(tmp_path: Path) -> None:
    booter = ComputerBooter(ComputerConfig(allow="all"), tmp_path)
    with pytest.raises(RunnerError, match="not been started"):
        await booter.exec(["echo", "no"], session_id="s", is_admin=True)


async def test_disabled_booter_is_unavailable(tmp_path: Path) -> None:
    booter = ComputerBooter(ComputerConfig(enable=False), tmp_path)
    status = await booter.start()
    assert not status.available
    assert status.error is not None
    assert "disabled" in status.error
    await booter.stop()


async def test_missing_binary_is_unavailable(tmp_path: Path) -> None:
    booter = ComputerBooter(ComputerConfig(), tmp_path, binary=tmp_path / "missing")
    status = await booter.start()
    assert not status.available
    assert status.error is not None


def test_windows_mode_selects_protocol_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("verter.core.computer.booter.sys.platform", "win32")
    assert ComputerBooter(ComputerConfig(windows_mode="standard"), tmp_path)._backend() == (
        "appcontainer"
    )
    assert ComputerBooter(ComputerConfig(windows_mode="isolated"), tmp_path)._backend() == (
        "isolated"
    )
    assert ComputerBooter(ComputerConfig(windows_mode="auto"), tmp_path)._backend() == "auto"
