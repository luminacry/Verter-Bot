"""In-process Telegram Bot API stand-in."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import cast

from aiohttp import web


def private_update(
    text: str = "/help",
    *,
    user_id: int = 111,
    update_id: int = 1,
    message_id: int = 1,
    mention: str | None = None,
) -> dict[str, object]:
    entities: list[dict[str, object]] = []
    if mention is not None:
        start = text.find(mention)
        if start >= 0:
            entities.append({"type": "mention", "offset": start, "length": len(mention)})
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": user_id, "is_bot": False, "first_name": "Ada", "username": "ada"},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
            "entities": entities,
        },
    }


def group_update(
    text: str = "/help",
    *,
    user_id: int = 111,
    chat_id: int = -100333,
    update_id: int = 2,
    photo_id: str | None = "AgAC",
    bot_id: int = 222,
) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": 2,
        "from": {"id": user_id, "is_bot": False, "first_name": "Ada"},
        "chat": {"id": chat_id, "type": "supergroup", "title": "test"},
        "text": text,
        "entities": [
            {
                "type": "text_mention",
                "offset": 0,
                "length": 0,
                "user": {"id": bot_id, "first_name": "Bot"},
            }
        ],
    }
    if photo_id is not None:
        message["photo"] = [{"file_id": photo_id, "width": 90, "height": 90}]
        message["caption"] = text
        message.pop("text", None)
    return {"update_id": update_id, "message": message}


class MockTelegram:
    def __init__(self, token: str = "123:abc") -> None:  # noqa: S107 - fake bot token, not a password
        self.token = token
        self.sent: list[dict[str, object]] = []
        self.fail_updates = 0
        self._updates: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._runner: web.AppRunner | None = None
        self.port: int | None = None

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post(f"/bot{self.token}/getMe", self._get_me)
        app.router.add_post(f"/bot{self.token}/getUpdates", self._get_updates)
        app.router.add_post(f"/bot{self.token}/sendMessage", self._send)
        app.router.add_post(f"/bot{self.token}/sendPhoto", self._send)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self._runner = runner
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.port = int(runner.addresses[0][1])
        return self.api_base

    async def push(self, update: Mapping[str, object]) -> None:
        await self._updates.put(dict(update))

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _get_me(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "result": {"id": 222, "is_bot": True, "username": "v"}}
        )

    async def _get_updates(self, request: web.Request) -> web.Response:
        if self.fail_updates > 0:
            self.fail_updates -= 1
            return web.json_response({"ok": False, "description": "upstream down"}, status=502)
        body = await _json(request)
        timeout = _as_float(body.get("timeout"))
        try:
            update = await asyncio.wait_for(self._updates.get(), timeout if timeout > 0 else 0.05)
        except TimeoutError:
            return web.json_response({"ok": True, "result": []})
        return web.json_response({"ok": True, "result": [update]})

    async def _send(self, request: web.Request) -> web.Response:
        payload = await _json(request)
        payload["method"] = request.path.rsplit("/", 1)[-1]
        self.sent.append(payload)
        return web.json_response({"ok": True, "result": {"message_id": 9}})


async def _json(request: web.Request) -> dict[str, object]:
    if request.can_read_body:
        data: object = await request.json()
        if isinstance(data, dict):
            return cast("dict[str, object]", data)
    return {}


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0
