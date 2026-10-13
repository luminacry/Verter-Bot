from pathlib import Path

from verter.core.db import Database


async def test_migration_plugin_state_and_kv(tmp_path: Path) -> None:
    db = Database(tmp_path / "verter.db")
    await db.connect()
    try:
        assert await db._user_version() == 1  # pyright: ignore[reportPrivateUsage]
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
        assert await again._user_version() == 1  # pyright: ignore[reportPrivateUsage]
        assert await again.plugin_enabled("helloworld") is False
        assert await again.kv_get("helloworld", "n") == {"ok": True}
    finally:
        await again.close()
