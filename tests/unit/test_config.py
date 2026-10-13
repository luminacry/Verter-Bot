import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from verter.core.config import (
    CONFIG_FILENAME,
    ENV_DATA_DIR,
    ComputerConfig,
    ConfigError,
    PlatformConfig,
    VerterConfig,
    initialize_data_dir,
    load_config,
    resolve_data_dir,
    write_config,
)
from verter.core.security import verify_password


def test_sandbox_defaults_to_admin_only_local_without_network() -> None:
    policy = VerterConfig().computer
    assert policy.enable
    assert policy.backend == "local"
    assert policy.allow == "admins"
    assert policy.network == "deny"
    assert policy.limits.timeout_s == 60
    assert policy.limits.memory_mb == 512


def test_dashboard_defaults_to_loopback() -> None:
    dashboard = VerterConfig().dashboard
    assert (dashboard.host, dashboard.port) == ("127.0.0.1", 6185)
    assert dashboard.password_hash == ""


@pytest.mark.parametrize(
    ("allow", "is_admin", "session_id", "expected"),
    [
        ("admins", True, "qq:GroupMessage:1", True),
        ("admins", False, "qq:GroupMessage:1", False),
        ("whitelist", False, "qq:GroupMessage:1", True),
        ("whitelist", False, "qq:GroupMessage:2", False),
        ("whitelist", True, "qq:GroupMessage:2", True),
        ("all", False, "tg:FriendMessage:9", True),
    ],
)
def test_sandbox_policy(allow: str, is_admin: bool, session_id: str, expected: bool) -> None:
    policy = ComputerConfig.model_validate({"allow": allow, "whitelist": ["qq:GroupMessage:1"]})
    assert policy.allows(is_admin=is_admin, session_id=session_id) is expected


def test_disabled_sandbox_denies_everyone() -> None:
    policy = ComputerConfig(enable=False, allow="all")
    assert not policy.allows(is_admin=True, session_id="any")


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    config = VerterConfig(admins_id=["10001"])
    config.computer.network = "allow"
    path = tmp_path / "nested" / CONFIG_FILENAME
    write_config(path, config)
    assert path.read_text(encoding="utf-8").startswith("# Verter-Bot configuration")
    assert load_config(path) == config
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


def test_assignment_is_validated() -> None:
    config = VerterConfig()
    with pytest.raises(ValidationError):
        config.dashboard.port = 70000


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("dashbord:\n  port: 1\n", "dashbord"),
        ("dashboard:\n  port: 70000\n", "dashboard.port"),
        ("computer:\n  allow: everyone\n", "computer.allow"),
        ("dashboard:\n  jwt_secret: short\n", "at least 32 characters"),
        ("key: [unclosed\n", "not valid YAML"),
        ("- a\n- b\n", "mapping"),
    ],
)
def test_load_rejects_bad_files(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / CONFIG_FILENAME
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_empty_file_yields_defaults(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_FILENAME
    path.write_text("", encoding="utf-8")
    assert load_config(path) == VerterConfig()


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / CONFIG_FILENAME)


def test_resolve_data_dir_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    assert resolve_data_dir() == (tmp_path / "data").resolve()
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "from-env"))
    assert resolve_data_dir() == (tmp_path / "from-env").resolve()
    assert resolve_data_dir(tmp_path / "explicit") == (tmp_path / "explicit").resolve()


def test_runtime_settings_reject_bad_values() -> None:
    with pytest.raises(ValidationError, match="wake prefixes"):
        VerterConfig(wake_prefix=[""])
    with pytest.raises(ValidationError, match="duplicate platform"):
        VerterConfig.model_validate(
            {"platform": [{"id": "a", "type": "console"}, {"id": "a", "type": "inmemory"}]}
        )
    with pytest.raises(ValidationError, match="regular expression"):
        VerterConfig.model_validate({"content_safety": {"keywords": ["("]}})


def test_platform_entry_keeps_adapter_settings() -> None:
    entry = PlatformConfig.model_validate({"id": "c", "type": "console", "sender_id": "me"})
    assert entry.settings == {"sender_id": "me"}
    assert VerterConfig().runtime.workers == 64
    assert VerterConfig().platform_settings.rate_limit.count == 30


def test_initialize_with_explicit_password(tmp_path: Path) -> None:
    result = initialize_data_dir(tmp_path, password="given-password")
    assert not result.password_generated
    config = load_config(result.config_path)
    assert verify_password("given-password", config.dashboard.password_hash)
    assert len(config.dashboard.jwt_secret) >= 43
    with pytest.raises(ConfigError, match="already exists"):
        initialize_data_dir(tmp_path)
