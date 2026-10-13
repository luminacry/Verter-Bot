import re
import sys
from pathlib import Path
from typing import Any

import pytest

from verter import __version__
from verter.cli import main as cli
from verter.core.config import CONFIG_FILENAME, ENV_ADMIN_PASSWORD, VerterConfig, load_config
from verter.core.security import verify_password


@pytest.fixture(autouse=True)
def no_env_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ADMIN_PASSWORD, raising=False)
    monkeypatch.setenv("VERTER_SANDBOX_BIN", str(Path(__file__)))


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_init_generates_a_password(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    match = re.search(r"Dashboard password: (\S+)", out)
    assert match is not None
    config = load_config(tmp_path / CONFIG_FILENAME)
    assert verify_password(match.group(1), config.dashboard.password_hash)


def test_init_uses_env_password_and_guards_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_ADMIN_PASSWORD, "from-env")
    assert cli.main(["init", "--data-dir", str(tmp_path)]) == 0
    assert "taken from $VERTER_ADMIN_PASSWORD" in capsys.readouterr().out
    assert cli.main(["init", "--data-dir", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["init", "--data-dir", str(tmp_path), "--force"]) == 0
    config = load_config(tmp_path / CONFIG_FILENAME)
    assert verify_password("from-env", config.dashboard.password_hash)


def test_doctor_handles_missing_valid_and_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["doctor", "--data-dir", str(tmp_path)]) == 0
    assert "missing (run `verter init`)" in capsys.readouterr().out

    cli.main(["init", "--data-dir", str(tmp_path)])
    capsys.readouterr()
    assert cli.main(["doctor", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "enabled, backend=local, allow=admins, network=deny" in out
    assert "Runner" in out

    (tmp_path / CONFIG_FILENAME).write_text("dashboard: []\n", encoding="utf-8")
    assert cli.main(["doctor", "--data-dir", str(tmp_path)]) == 1
    assert "invalid" in capsys.readouterr().out


class FakeServe:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[VerterConfig, Any]] = []

    async def __call__(self, config: VerterConfig, *, webui_dist: Path | None = None) -> None:
        self.calls.append((config, webui_dist))
        if self.error is not None:
            raise self.error


@pytest.mark.usefixtures("restore_logging")
def test_run_initializes_on_first_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeServe()
    monkeypatch.setattr(cli, "serve", fake)
    code = cli.main(["run", "--data-dir", str(tmp_path), "--host", "127.0.0.2", "--port", "0"])
    assert code == 0
    assert "First run" in capsys.readouterr().out
    config, webui_dist = fake.calls[0]
    assert (config.dashboard.host, config.dashboard.port) == ("127.0.0.2", 0)
    assert webui_dist is None or (webui_dist / "index.html").is_file()


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize(
    ("error", "expected"), [(KeyboardInterrupt(), 0), (OSError("address in use"), 1)]
)
def test_run_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, expected: int
) -> None:
    monkeypatch.setattr(cli, "serve", FakeServe(error))
    assert cli.main(["run", "--data-dir", str(tmp_path)]) == expected


@pytest.mark.parametrize(
    "extra",
    [["--port", "70000"], ["--webui-dist", "definitely/missing"]],
    ids=["port-out-of-range", "missing-webui"],
)
def test_run_rejects_bad_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    cli.main(["init", "--data-dir", str(tmp_path)])
    assert cli.main(["run", "--data-dir", str(tmp_path), *extra]) == 2
    assert "error:" in capsys.readouterr().err


def test_run_rejects_invalid_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("log_level: LOUD\n", encoding="utf-8")
    assert cli.main(["run", "--data-dir", str(tmp_path)]) == 2
    assert "log_level" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_config_file_is_private(tmp_path: Path) -> None:
    cli.main(["init", "--data-dir", str(tmp_path)])
    assert (tmp_path / CONFIG_FILENAME).stat().st_mode & 0o777 == 0o600
