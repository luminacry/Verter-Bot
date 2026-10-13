"""Events and handler filters."""

from verter.core.platform.message_event import MessageEvent as MessageEvent
from verter.core.star.filter import (
    PermissionType as PermissionType,
)
from verter.core.star.filter import (
    after_message_sent as after_message_sent,
)
from verter.core.star.filter import (
    command as command,
)
from verter.core.star.filter import (
    command_group as command_group,
)
from verter.core.star.filter import (
    custom_filter as custom_filter,
)
from verter.core.star.filter import (
    event_message_type as event_message_type,
)
from verter.core.star.filter import (
    on_decorating_result as on_decorating_result,
)
from verter.core.star.filter import (
    permission_type as permission_type,
)
from verter.core.star.filter import (
    platform_adapter_type as platform_adapter_type,
)
from verter.core.star.filter import (
    regex as regex,
)

# A small alias so an AstrBot handler can be renamed without changing its type.
AstrMessageEvent = MessageEvent
