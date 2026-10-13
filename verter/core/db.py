"""SQLite storage for plugin state and sandbox audit rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_MIGRATIONS = (
    """
    CREATE TABLE plugin_state (
        name TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL
    );
    CREATE TABLE plugin_kv (
        plugin TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (plugin, key)
    );
    """,
    """
    CREATE TABLE sandbox_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        session_id TEXT NOT NULL,
        initiator TEXT NOT NULL,
        command TEXT NOT NULL,
        allowed INTEGER NOT NULL,
        exit_code INTEGER,
        timed_out INTEGER,
        backend TEXT,
        wall_ms INTEGER,
        truncated INTEGER NOT NULL DEFAULT 0,
        output TEXT,
        error TEXT
    );
    """,
)


@dataclass(frozen=True, slots=True)
class SandboxAuditRow:
    id: int
    created_at: str
    session_id: str
    initiator: str
    command: str
    allowed: bool
    exit_code: int | None
    timed_out: bool | None
    backend: str | None
    wall_ms: int | None
    truncated: bool
    output: str | None
    error: str | None


class Database:
    """One process-wide connection in WAL mode.

    Schema changes are applied in order. ``PRAGMA user_version`` is the version
    already applied, starting at 0 on a new file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            await self._migrate()
        except Exception:
            await conn.close()
            self._conn = None
            raise

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    async def plugin_enabled(self, name: str, *, default: bool = True) -> bool:
        row = await self._one("SELECT enabled FROM plugin_state WHERE name = ?", (name,))
        if row is None:
            return default
        return bool(row[0])

    async def set_plugin_enabled(self, name: str, enabled: bool) -> None:
        await self._conn_execute(
            "INSERT INTO plugin_state (name, enabled) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled",
            (name, int(enabled)),
        )

    async def kv_get(self, plugin: str, key: str, default: Any = None) -> Any:
        row = await self._one(
            "SELECT value FROM plugin_kv WHERE plugin = ? AND key = ?", (plugin, key)
        )
        if row is None:
            return default
        return json.loads(row[0])

    async def record_sandbox_audit(
        self,
        *,
        session_id: str,
        initiator: str,
        command: str,
        allowed: bool,
        exit_code: int | None = None,
        timed_out: bool | None = None,
        backend: str | None = None,
        wall_ms: int | None = None,
        truncated: bool = False,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._conn_execute(
            "INSERT INTO sandbox_audit ("
            "created_at, session_id, initiator, command, allowed, exit_code, "
            "timed_out, backend, wall_ms, truncated, output, error"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                session_id,
                initiator,
                command,
                int(allowed),
                exit_code,
                None if timed_out is None else int(timed_out),
                backend,
                wall_ms,
                int(truncated),
                output,
                error,
            ),
        )

    async def list_sandbox_audit(self, limit: int = 100) -> list[SandboxAuditRow]:
        conn = self._require()
        async with conn.execute(
            "SELECT id, created_at, session_id, initiator, command, allowed, "
            "exit_code, timed_out, backend, wall_ms, truncated, output, error "
            "FROM sandbox_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            SandboxAuditRow(
                id=int(row[0]),
                created_at=str(row[1]),
                session_id=str(row[2]),
                initiator=str(row[3]),
                command=str(row[4]),
                allowed=bool(row[5]),
                exit_code=None if row[6] is None else int(row[6]),
                timed_out=None if row[7] is None else bool(row[7]),
                backend=None if row[8] is None else str(row[8]),
                wall_ms=None if row[9] is None else int(row[9]),
                truncated=bool(row[10]),
                output=None if row[11] is None else str(row[11]),
                error=None if row[12] is None else str(row[12]),
            )
            for row in rows
        ]

    async def kv_set(self, plugin: str, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        await self._conn_execute(
            "INSERT INTO plugin_kv (plugin, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(plugin, key) DO UPDATE SET value = excluded.value",
            (plugin, key, encoded),
        )

    async def _migrate(self) -> None:
        conn = self._require()
        version = await self._user_version()
        for index, sql in enumerate(_MIGRATIONS, start=1):
            if version >= index:
                continue
            await conn.executescript(sql)
            await conn.execute(f"PRAGMA user_version = {index}")
            await conn.commit()

    async def _user_version(self) -> int:
        row = await self._one("PRAGMA user_version")
        return int(row[0]) if row is not None else 0

    async def _one(self, sql: str, params: tuple[object, ...] = ()) -> tuple[Any, ...] | None:
        conn = self._require()
        async with conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return tuple(row)

    async def _conn_execute(self, sql: str, params: tuple[object, ...]) -> None:
        conn = self._require()
        await conn.execute(sql, params)
        await conn.commit()

    def _require(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn
