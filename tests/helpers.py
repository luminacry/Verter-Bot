"""Pipeline test doubles shared by unit and integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

from verter.core.config import PlatformConfig, VerterConfig
from verter.core.message.message_event_result import MessageEventResult
from verter.core.pipeline.context import BoundHandler, HookPoint, PipelineContext
from verter.core.pipeline.scheduler import PipelineScheduler
from verter.core.platform.message_event import MessageEvent
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.inmemory import InMemoryPlatform, SentMessage

type HandlerFn = Callable[[MessageEvent], AsyncIterator[MessageEventResult | None]]


def reply(text: str) -> HandlerFn:
    async def handler(event: MessageEvent) -> AsyncIterator[MessageEventResult | None]:
        yield event.plain_result(text)

    return handler


@dataclass
class ScriptedHandlers:
    """Routes woken events by their first word; ``listeners`` match any message."""

    commands: dict[str, HandlerFn | list[HandlerFn]] = field(
        default_factory=dict[str, HandlerFn | list[HandlerFn]]
    )
    listeners: list[HandlerFn] = field(default_factory=list[HandlerFn])
    hook_calls: list[tuple[HookPoint, str]] = field(default_factory=list[tuple[HookPoint, str]])
    hook_actions: dict[HookPoint, Callable[[MessageEvent], None]] = field(
        default_factory=dict[HookPoint, Callable[[MessageEvent], None]]
    )

    async def match(self, event: MessageEvent) -> Sequence[BoundHandler]:
        matched: list[BoundHandler] = []
        if event.is_at_or_wake_command:
            word = event.message_str.split(" ", 1)[0]
            routed = self.commands.get(word, [])
            for index, fn in enumerate(routed if isinstance(routed, list) else [routed]):
                matched.append(BoundHandler(f"cmd.{word}.{index}", fn))
        matched.extend(BoundHandler(f"listener.{i}", fn) for i, fn in enumerate(self.listeners))
        return matched

    async def run_hooks(self, hook: HookPoint, event: MessageEvent) -> None:
        self.hook_calls.append((hook, event.message_str))
        action = self.hook_actions.get(hook)
        if action is not None:
            action(event)


class NullSink:
    def commit(self, event: MessageEvent) -> bool:
        return True


def make_inmemory(platform_id: str = "mem") -> InMemoryPlatform:
    context = PlatformContext(sink=NullSink(), request_shutdown=lambda: None)
    return InMemoryPlatform(PlatformConfig(id=platform_id, type="inmemory"), context)


@dataclass
class Harness:
    """Runs events straight through a scheduler, bypassing the event bus."""

    scheduler: PipelineScheduler
    platform: InMemoryPlatform
    handlers: ScriptedHandlers

    @property
    def ctx(self) -> PipelineContext:
        return self.scheduler.ctx

    async def run(self, event: MessageEvent) -> list[SentMessage]:
        await self.scheduler.execute(event)
        sent: list[SentMessage] = []
        while not self.platform.sent.empty():
            sent.append(self.platform.sent.get_nowait())
        return sent

    async def send(
        self,
        text: str = "",
        *,
        sender_id: str = "user",
        sender_name: str = "",
        group_id: str | None = None,
    ) -> list[str]:
        event = self.platform.build_event(
            text, sender_id=sender_id, sender_name=sender_name, group_id=group_id
        )
        return [m.chain.outline() for m in await self.run(event)]


async def make_harness(
    config: VerterConfig | None = None, handlers: ScriptedHandlers | None = None
) -> Harness:
    if config is None:
        config = VerterConfig()
        config.platform_settings.rate_limit.enable = False
    handlers = handlers or ScriptedHandlers()
    scheduler = PipelineScheduler(PipelineContext(config, handlers))
    await scheduler.initialize()
    return Harness(scheduler, make_inmemory(), handlers)
