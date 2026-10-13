"""Drops events from conversations outside ``platform_settings.id_whitelist`` when enabled.

Entries match a unified message origin or a bare session id.
"""

from __future__ import annotations

from verter.core.log import logger
from verter.core.pipeline.context import PipelineContext
from verter.core.pipeline.stage import Stage, register_stage
from verter.core.platform.message_event import MessageEvent


@register_stage
class WhitelistCheckStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        settings = ctx.config.platform_settings
        self._enabled = settings.enable_id_white_list
        self._allowed = frozenset(settings.id_whitelist)
        self._admin_bypass_group = settings.wl_ignore_admin_on_group
        self._admin_bypass_friend = settings.wl_ignore_admin_on_friend

    async def process(self, event: MessageEvent) -> None:
        if not self._enabled:
            return
        if event.is_admin():
            bypass = (
                self._admin_bypass_friend if event.is_private_chat() else self._admin_bypass_group
            )
            if bypass:
                return
        origin = event.unified_msg_origin
        if origin in self._allowed or event.session_id in self._allowed:
            return
        logger.info("Ignoring %s: not in id_whitelist", origin)
        event.stop_event()
