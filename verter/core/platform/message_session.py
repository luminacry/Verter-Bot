from __future__ import annotations

from dataclasses import dataclass

from verter.core.platform.message_type import MessageType


@dataclass(frozen=True, slots=True)
class MessageSession:
    """Address of a conversation; ``str()`` gives its unified message origin."""

    platform_id: str
    message_type: MessageType
    session_id: str

    def __str__(self) -> str:
        return f"{self.platform_id}:{self.message_type.value}:{self.session_id}"

    @classmethod
    def from_str(cls, origin: str) -> MessageSession:
        """Parse ``platform_id:MessageType:session_id``.

        Raises:
            ValueError: If ``origin`` is not a unified message origin.
        """
        parts = origin.split(":", 2)
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise ValueError(f"not a unified message origin: {origin!r}")
        return cls(parts[0], MessageType(parts[1]), parts[2])
