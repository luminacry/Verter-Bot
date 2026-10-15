from enum import StrEnum


class MessageType(StrEnum):
    """Conversation kind. The values appear in unified message origins."""

    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"
    OTHER_MESSAGE = "OtherMessage"
    NOTICE = "Notice"
    REQUEST = "Request"
