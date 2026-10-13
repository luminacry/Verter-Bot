from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlatformMetadata:
    name: str
    """Adapter type, such as ``console`` or ``onebot_v11``."""
    id: str
    """Configured instance id; unique across platforms."""
    description: str = ""
