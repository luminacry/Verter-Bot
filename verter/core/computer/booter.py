"""Resident sandbox process, session workspaces, and permission checks."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from verter.core.computer.protocol import ExecBackend
from verter.core.computer.runner import (
    ExecResult,
    RunnerError,
    RunnerStatus,
    SandboxClient,
    resolve_runner_binary,
)
from verter.core.config import ComputerConfig
from verter.core.log import logger


class SandboxDeniedError(RunnerError):
    """The session is not allowed to run sandboxed commands."""


class ComputerBooter:
    """Keeps ``verter-sandbox serve`` running and prepares per-session workspaces."""

    def __init__(
        self,
        config: ComputerConfig,
        data_dir: Path,
        *,
        binary: Path | None = None,
    ) -> None:
        self._config = config
        self._data_dir = data_dir
        self._binary = binary
        self._client: SandboxClient | None = None
        self._status = RunnerStatus(available=False, error="sandbox has not been started")

    @property
    def status(self) -> RunnerStatus:
        return self._status

    async def start(self) -> RunnerStatus:
        """Spawn a resident runner and handshake. Missing binaries are reported, not raised."""
        if not self._config.enable:
            self._status = RunnerStatus(available=False, error="sandbox disabled in config")
            return self._status
        binary = self._binary if self._binary is not None else resolve_runner_binary()
        if binary is None:
            self._status = RunnerStatus(available=False, error="verter-sandbox not found")
            return self._status
        timeout = float(self._config.limits.timeout_s) + 5.0
        client = SandboxClient(binary, timeout=timeout)
        try:
            ack = await client.start()
        except RunnerError as exc:
            await client.close()
            self._status = RunnerStatus(available=False, path=binary, error=str(exc))
            logger.warning("Sandbox runner unavailable: %s", exc)
            return self._status
        self._client = client
        self._status = RunnerStatus(
            available=True,
            path=binary,
            version=ack.server_version,
            backends=tuple(ack.platform.backends),
        )
        logger.info(
            "Sandbox runner %s ready (%s, allow=%s, network=%s)",
            ack.server_version,
            binary,
            self._config.allow,
            self._config.network,
        )
        return self._status

    async def stop(self) -> None:
        """Ask the resident runner to exit."""
        client, self._client = self._client, None
        if client is not None:
            await client.close()
        if self._status.available:
            self._status = RunnerStatus(
                available=False,
                path=self._status.path,
                version=self._status.version,
                backends=self._status.backends,
                error="sandbox stopped",
            )

    def workspace(self, session_id: str) -> Path:
        """Return ``data/workspaces/<sha256[:16]>/``, creating it if needed."""
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        path = (self._data_dir / "workspaces" / digest).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def exec(
        self,
        command: list[str],
        *,
        session_id: str,
        is_admin: bool,
    ) -> ExecResult:
        """Run ``command`` if the session is allowed.

        Raises:
            SandboxDeniedError: If the configured policy rejects the caller.
            RunnerError: If the runner is down or isolation refuses the command.
        """
        if not self._config.allows(is_admin=is_admin, session_id=session_id):
            raise SandboxDeniedError(
                f"sandbox not allowed for session {session_id!r} (allow={self._config.allow})"
            )
        client = self._client
        if client is None or not self._status.available:
            raise RunnerError(self._status.error or "sandbox is not running")
        limits = self._config.limits
        return await client.exec(
            command,
            workspace=self.workspace(session_id),
            backend=self._backend(),
            network=self._config.network == "allow",
            timeout_ms=limits.timeout_s * 1000,
            memory_bytes=limits.memory_mb * 1024 * 1024,
            nproc=limits.max_procs,
            output_limit=limits.output_kb * 1024,
        )

    def _backend(self) -> ExecBackend:
        if sys.platform == "win32":
            if self._config.windows_mode == "standard":
                return "appcontainer"
            if self._config.windows_mode == "isolated":
                return "isolated"
        return "auto"
