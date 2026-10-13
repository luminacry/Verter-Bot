"""Client for the ``verter-sandbox`` runner process.

The runner speaks newline-delimited JSON over stdio; message types live in
:mod:`verter.core.computer.protocol`, which is generated from the Rust crate.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import TypeAdapter, ValidationError

from verter import __version__
from verter.core.computer.protocol import (
    BackendStatus,
    ClientMessage,
    ErrorReply,
    Exec,
    ExecBackend,
    ExecExit,
    ExecStderr,
    ExecStdout,
    Hello,
    HelloAck,
    Ping,
    Pong,
    ServerMessage,
    Shutdown,
)

PROTOCOL_VERSION = 2
ENV_RUNNER_BIN = "VERTER_SANDBOX_BIN"
RUNNER_NAME = "verter-sandbox"
DEFAULT_OUTPUT_LIMIT = 64 * 1024

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SERVER_MESSAGE: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
_CLIENT_MESSAGE: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


class RunnerError(RuntimeError):
    """The runner could not be started or violated the protocol."""


@dataclass(frozen=True, slots=True)
class RunnerStatus:
    available: bool
    path: Path | None = None
    version: str | None = None
    backends: tuple[BackendStatus, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Collected output of one sandboxed ``exec``."""

    exit_code: int
    timed_out: bool
    backend: str
    wall_ms: int
    stdout: bytes
    stderr: bytes
    truncated: bool


def resolve_runner_binary() -> Path | None:
    """Locate the runner: ``VERTER_SANDBOX_BIN``, then ``PATH``, then a local cargo build."""
    explicit = os.environ.get(ENV_RUNNER_BIN)
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    found = shutil.which(RUNNER_NAME)
    if found:
        return Path(found)
    for profile in ("release", "debug"):
        candidate = _REPO_ROOT / "sandbox" / "target" / profile / (RUNNER_NAME + _EXE_SUFFIX)
        if candidate.is_file():
            return candidate
    return None


class SandboxClient:
    """One ``verter-sandbox serve`` process speaking the NDJSON protocol."""

    def __init__(self, binary: Path, *, timeout: float = 5.0) -> None:
        self._binary = binary
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> HelloAck:
        """Spawn the runner and perform the handshake.

        Raises:
            RunnerError: If the process cannot start or the handshake fails.
        """
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._binary,
                "serve",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RunnerError(f"cannot start {self._binary}: {exc}") from exc
        reply = await self._request(
            Hello(type="hello", client=f"verter/{__version__}", protocol_version=PROTOCOL_VERSION)
        )
        if not isinstance(reply, HelloAck):
            raise RunnerError(f"handshake rejected: {_describe(reply)}")
        if reply.protocol_version != PROTOCOL_VERSION:
            raise RunnerError(
                f"runner speaks protocol {reply.protocol_version}, expected {PROTOCOL_VERSION}"
            )
        return reply

    async def ping(self, nonce: int) -> None:
        """Round-trip a ping and check the echoed nonce."""
        reply = await self._request(Ping(type="ping", nonce=nonce))
        if not isinstance(reply, Pong) or reply.nonce != nonce:
            raise RunnerError(f"unexpected reply to ping: {_describe(reply)}")

    async def exec(
        self,
        command: list[str],
        *,
        workspace: Path,
        backend: ExecBackend = "auto",
        network: bool = False,
        timeout_ms: int = 30_000,
        memory_bytes: int = 512 * 1024 * 1024,
        nproc: int = 256,
        output_limit: int = DEFAULT_OUTPUT_LIMIT,
    ) -> ExecResult:
        """Run ``command`` inside the sandbox and collect streamed output.

        Isolation refusals are raised as :class:`RunnerError`. The command is never
        executed on the host.

        Raises:
            RunnerError: If the runner is down, the protocol is violated, or isolation
                refuses the request.
        """
        exec_id = self._next_id
        self._next_id += 1
        await self._send(
            Exec(
                type="exec",
                id=exec_id,
                command=command,
                workspace=str(workspace),
                backend=backend,
                network=network,
                timeout_ms=timeout_ms,
                memory_bytes=memory_bytes,
                nproc=nproc,
            )
        )
        stdout = bytearray()
        stderr = bytearray()
        truncated = False
        read_timeout = max(self._timeout, timeout_ms / 1000.0 + 5.0)
        while True:
            reply = await self._recv(wait_s=read_timeout)
            if isinstance(reply, ErrorReply):
                raise RunnerError(_describe(reply))
            if isinstance(reply, ExecStdout):
                chunk = _decode_chunk(reply.chunk_b64)
                truncated = _append_limited(stdout, chunk, output_limit) or truncated
            elif isinstance(reply, ExecStderr):
                chunk = _decode_chunk(reply.chunk_b64)
                truncated = _append_limited(stderr, chunk, output_limit) or truncated
            elif isinstance(reply, ExecExit):
                if reply.id != exec_id:
                    raise RunnerError(f"exec_exit id {reply.id} does not match {exec_id}")
                return ExecResult(
                    exit_code=reply.exit_code,
                    timed_out=reply.timed_out,
                    backend=reply.backend,
                    wall_ms=reply.wall_ms,
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    truncated=truncated,
                )
            else:
                raise RunnerError(f"unexpected reply to exec: {_describe(reply)}")

    async def close(self) -> None:
        """Ask the runner to exit, killing it if it does not comply in time."""
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        if proc.stdin is not None:
            with contextlib.suppress(ConnectionError):
                proc.stdin.write(_encode(Shutdown(type="shutdown")))
                await proc.stdin.drain()
                proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def _request(self, message: ClientMessage) -> ServerMessage:
        await self._send(message)
        return await self._recv(wait_s=self._timeout)

    async def _send(self, message: ClientMessage) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RunnerError("runner is not running")
        try:
            proc.stdin.write(_encode(message))
            await proc.stdin.drain()
        except (ConnectionError, TimeoutError) as exc:
            raise RunnerError(f"runner did not answer: {exc!r}") from exc

    async def _recv(self, *, wait_s: float) -> ServerMessage:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RunnerError("runner is not running")
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), wait_s)
        except (ConnectionError, TimeoutError) as exc:
            raise RunnerError(f"runner did not answer: {exc!r}") from exc
        if not line:
            raise RunnerError(f"runner exited with code {await proc.wait()}")
        try:
            return _SERVER_MESSAGE.validate_json(line)
        except ValidationError as exc:
            raise RunnerError(f"invalid runner message: {line[:200]!r}") from exc


async def probe_runner(binary: Path | None, *, reply_timeout: float = 5.0) -> RunnerStatus:
    """Start the runner once to report its version and isolation backends."""
    if binary is None:
        return RunnerStatus(available=False, error=f"{RUNNER_NAME} not found")
    try:
        async with SandboxClient(binary, timeout=reply_timeout) as client:
            ack = await client.start()
    except RunnerError as exc:
        return RunnerStatus(available=False, path=binary, error=str(exc))
    return RunnerStatus(
        available=True,
        path=binary,
        version=ack.server_version,
        backends=tuple(ack.platform.backends),
    )


def _encode(message: ClientMessage) -> bytes:
    return _CLIENT_MESSAGE.dump_json(message) + b"\n"


def _describe(reply: ServerMessage) -> str:
    if isinstance(reply, ErrorReply):
        return f"{reply.code}: {reply.message}"
    return reply.type


def _decode_chunk(chunk_b64: str) -> bytes:
    try:
        return base64.b64decode(chunk_b64, validate=True)
    except ValueError as exc:
        raise RunnerError(f"invalid exec chunk: {exc}") from exc


def _append_limited(buf: bytearray, chunk: bytes, limit: int) -> bool:
    if limit <= 0:
        return bool(chunk)
    room = limit - len(buf)
    if room <= 0:
        return bool(chunk)
    buf.extend(chunk[:room])
    return len(chunk) > room
