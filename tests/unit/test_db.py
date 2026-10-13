from pathlib import Path

from verter.core.db import Database


async def test_migration_plugin_state_and_kv(tmp_path: Path) -> None:
    db = Database(tmp_path / "verter.db")
    await db.connect()
    try:
        assert await db._user_version() == 2  # pyright: ignore[reportPrivateUsage]
        assert await db.plugin_enabled("helloworld") is True
        await db.set_plugin_enabled("helloworld", False)
        assert await db.plugin_enabled("helloworld") is False
        assert await db.kv_get("helloworld", "missing", "d") == "d"
        await db.kv_set("helloworld", "n", {"ok": True})
        assert await db.kv_get("helloworld", "n") == {"ok": True}
    finally:
        await db.close()

    again = Database(tmp_path / "verter.db")
    await again.connect()
    try:
        assert await again._user_version() == 2  # pyright: ignore[reportPrivateUsage]
        assert await again.plugin_enabled("helloworld") is False
        assert await again.kv_get("helloworld", "n") == {"ok": True}
    finally:
        await again.close()


async def test_sandbox_audit_round_trip(tmp_path: Path) -> None:
    db = Database(tmp_path / "verter.db")
    await db.connect()
    try:
        await db.record_sandbox_audit(
            session_id="s1",
            initiator="alice",
            command="echo hi",
            allowed=False,
            error="sandbox not allowed",
        )
        await db.record_sandbox_audit(
            session_id="s1",
            initiator="alice",
            command="echo ok",
            allowed=True,
            exit_code=0,
            timed_out=False,
            backend="bubblewrap",
            wall_ms=12,
            truncated=False,
            output="ok\n",
        )
        rows = await db.list_sandbox_audit(10)
        assert len(rows) == 2
        assert rows[0].command == "echo ok"
        assert rows[0].allowed is True
        assert rows[0].backend == "bubblewrap"
        assert rows[1].allowed is False
        assert rows[1].error == "sandbox not allowed"
    finally:
        await db.close()
