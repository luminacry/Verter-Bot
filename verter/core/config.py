"""User configuration: schema, loading, and first-run initialization."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from verter.core.security import generate_password, generate_secret, hash_password

CONFIG_FILENAME = "config.yaml"
ENV_DATA_DIR = "VERTER_DATA_DIR"
ENV_ADMIN_PASSWORD = "VERTER_ADMIN_PASSWORD"  # noqa: S105 - environment variable name

_HEADER = "# Verter-Bot configuration. Unknown keys are rejected at startup.\n"


class ConfigError(Exception):
    """Raised when the configuration file cannot be read or fails validation."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DashboardConfig(_Strict):
    enable: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=6185, ge=0, le=65535)
    username: str = Field(default="admin", min_length=1, max_length=64)
    password_hash: str = ""
    jwt_secret: str = ""
    session_ttl_hours: int = Field(default=24, ge=1, le=24 * 30)
    cookie_secure: bool = False

    @field_validator("jwt_secret")
    @classmethod
    def _check_secret_length(cls, value: str) -> str:
        if value and len(value) < 32:
            raise ValueError("must be empty (generated at startup) or at least 32 characters")
        return value


class SandboxLimits(_Strict):
    timeout_s: int = Field(default=60, ge=1, le=3600)
    cpu_s: int = Field(default=30, ge=1, le=3600)
    memory_mb: int = Field(default=512, ge=32, le=65536)
    max_procs: int = Field(default=64, ge=1, le=4096)
    output_kb: int = Field(default=64, ge=1, le=10240)


class ComputerConfig(_Strict):
    """Agent sandbox policy. Defaults to a local sandbox that only admins may use."""

    enable: bool = True
    backend: Literal["local"] = "local"
    allow: Literal["admins", "whitelist", "all"] = "admins"
    whitelist: list[str] = Field(default_factory=list[str])
    network: Literal["deny", "allow"] = "deny"
    windows_mode: Literal["auto", "standard", "isolated"] = "auto"
    limits: SandboxLimits = Field(default_factory=SandboxLimits)

    def allows(self, *, is_admin: bool, session_id: str) -> bool:
        """Return whether a sender in the given session may invoke sandbox tools.

        Args:
            is_admin: Whether the sender is listed in ``admins_id``.
            session_id: Unified session identifier of the conversation.

        Returns:
            True if the configured policy permits the call.
        """
        if not self.enable:
            return False
        if self.allow == "all" or is_admin:
            return True
        return self.allow == "whitelist" and session_id in self.whitelist


class RateLimitSettings(_Strict):
    """Per-session limit on messages that wake the bot."""

    enable: bool = True
    count: int = Field(default=30, ge=1)
    window_s: float = Field(default=60.0, gt=0)
    strategy: Literal["discard", "stall"] = "discard"


class PlatformSettings(_Strict):
    """Message handling shared by all platforms; key names follow AstrBot's platform_settings."""

    friend_message_needs_wake_prefix: bool = False
    ignore_bot_self_message: bool = True
    ignore_at_all: bool = False
    enable_id_white_list: bool = False
    id_whitelist: list[str] = Field(default_factory=list[str])
    wl_ignore_admin_on_group: bool = True
    wl_ignore_admin_on_friend: bool = True
    reply_prefix: str = ""
    reply_with_mention: bool = False
    reply_with_quote: bool = False
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)


class ContentSafetyConfig(_Strict):
    enable: bool = False
    keywords: list[str] = Field(default_factory=list[str])
    also_use_in_response: bool = True

    @field_validator("keywords")
    @classmethod
    def _check_patterns(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
        return value


class RuntimeConfig(_Strict):
    max_pending_events: int = Field(default=10_000, ge=1)
    workers: int = Field(default=64, ge=1, le=4096)
    event_timeout_s: float = Field(default=300.0, ge=0, description="0 disables the timeout")


class PlatformConfig(BaseModel):
    """One adapter instance. Adapter-specific keys are validated by the adapter itself."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: str = Field(min_length=1)
    enable: bool = True

    @property
    def settings(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class VerterConfig(_Strict):
    config_version: Literal[1] = 1
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    admins_id: list[str] = Field(default_factory=list[str])
    wake_prefix: list[str] = Field(default_factory=lambda: ["/"])
    platform_settings: PlatformSettings = Field(default_factory=PlatformSettings)
    content_safety: ContentSafetyConfig = Field(default_factory=ContentSafetyConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    platform: list[PlatformConfig] = Field(default_factory=list[PlatformConfig])
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    computer: ComputerConfig = Field(default_factory=ComputerConfig)

    @field_validator("wake_prefix")
    @classmethod
    def _check_wake_prefix(cls, value: list[str]) -> list[str]:
        if any(not prefix for prefix in value):
            raise ValueError("wake prefixes must not be empty strings")
        return value

    @field_validator("platform")
    @classmethod
    def _check_unique_ids(cls, value: list[PlatformConfig]) -> list[PlatformConfig]:
        ids = [p.id for p in value]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate platform ids: {', '.join(duplicates)}")
        return value


@dataclass(frozen=True, slots=True)
class InitResult:
    config_path: Path
    username: str
    password: str
    password_generated: bool


def resolve_data_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the data directory from an explicit value, ``VERTER_DATA_DIR``, or ``./data``."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "data").resolve()


def load_config(path: Path) -> VerterConfig:
    """Load and validate a configuration file.

    Args:
        path: Path to ``config.yaml``.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file is missing, is not valid YAML, or fails validation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data: Any = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))  # noqa: S506
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    try:
        return VerterConfig.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {details}") from exc


def write_config(path: Path, config: VerterConfig) -> None:
    """Atomically write the configuration, readable only by the owner on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_HEADER + body, encoding="utf-8", newline="\n")
    if sys.platform != "win32":
        tmp.chmod(0o600)
    tmp.replace(path)


def initialize_data_dir(
    data_dir: Path, *, password: str | None = None, force: bool = False
) -> InitResult:
    """Create the data directory with a fresh configuration and dashboard credentials.

    Args:
        data_dir: Target data directory.
        password: Dashboard admin password. Falls back to ``VERTER_ADMIN_PASSWORD``,
            then to a randomly generated password.
        force: Overwrite an existing configuration file.

    Returns:
        The written config path and the admin credentials.

    Raises:
        ConfigError: If a configuration already exists and ``force`` is False.
    """
    config_path = data_dir / CONFIG_FILENAME
    if config_path.exists() and not force:
        raise ConfigError(f"{config_path} already exists; pass --force to overwrite it")
    password = password or os.environ.get(ENV_ADMIN_PASSWORD) or None
    generated = password is None
    if password is None:
        password = generate_password()
    config = VerterConfig()
    config.dashboard.password_hash = hash_password(password)
    config.dashboard.jwt_secret = generate_secret()
    write_config(config_path, config)
    return InitResult(config_path, config.dashboard.username, password, generated)
