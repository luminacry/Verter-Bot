"""Objects a plugin loaded from disk can import during tests."""

from __future__ import annotations

import asyncio

ready: asyncio.Event | None = None
cancelled: list[str] = []
