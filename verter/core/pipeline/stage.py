from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable
from typing import ClassVar

from verter.core.pipeline.context import PipelineContext
from verter.core.platform.message_event import MessageEvent

registered_stages: dict[str, type[Stage]] = {}


def register_stage[S: Stage](cls: type[S]) -> type[S]:
    """Make a stage available to the scheduler under its class name."""
    registered_stages[cls.__name__] = cls
    return cls


class Stage(ABC):
    """One step of the message pipeline.

    ``process`` is a coroutine function, or an async generator function: each
    ``yield`` runs every later stage before the generator resumes (onion model).
    """

    delivers: ClassVar[bool] = False
    """Delivery stages still run after ``stop_event()`` so yielded results are sent."""

    @abstractmethod
    async def initialize(self, ctx: PipelineContext) -> None: ...

    @abstractmethod
    def process(self, event: MessageEvent) -> Awaitable[None] | AsyncIterator[None]: ...
