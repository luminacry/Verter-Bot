import pytest

from verter.core.config import ConfigError, PlatformConfig
from verter.core.message.components import At, Image, Plain, Reply
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.telegram import TelegramPlatform, segments_from_telegram


class _Sink:
    def commit(self, event: object) -> bool:
        return True


def test_text_mention_photo_and_reply() -> None:
    parts = segments_from_telegram(
        {
            "text": "hello @bot",
            "entities": [{"type": "mention", "offset": 6, "length": 4}],
            "reply_to_message": {"message_id": 8},
            "photo": [{"file_id": "small"}, {"file_id": "large", "width": 100}],
        }
    )
    assert parts[0] == Reply("8")
    assert parts[1] == At("bot", "@bot")
    assert parts[2] == Plain("hello @bot")
    assert parts[3] == Image("large")


def test_caption_and_text_mention_user() -> None:
    parts = segments_from_telegram(
        {
            "caption": "hi",
            "caption_entities": [
                {
                    "type": "text_mention",
                    "offset": 0,
                    "length": 2,
                    "user": {"id": 7, "first_name": "Ada"},
                }
            ],
        }
    )
    assert parts[0] == At("7", "Ada")
    assert parts[1] == Plain("hi")


def test_empty_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(ConfigError, match="token"):
        TelegramPlatform(
            PlatformConfig.model_validate(
                {"id": "tg", "type": "telegram", "token": "ENV:TELEGRAM_BOT_TOKEN"}
            ),
            PlatformContext(sink=_Sink(), request_shutdown=lambda: None),
        )
