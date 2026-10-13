import os

import pytest

from verter.core.config import ConfigError, PlatformConfig
from verter.core.message.components import At, AtAll, Face, Image, Plain, Record, Reply, Unknown
from verter.core.message.message_event_result import MessageChain
from verter.core.platform.platform import PlatformContext
from verter.core.platform.sources.onebot_v11 import (
    OneBotV11Platform,
    resolve_secret,
    segments_from_onebot,
    segments_to_onebot,
)


class _Sink:
    def commit(self, event: object) -> bool:
        return True


def test_text_at_image_and_unknown_round_trip() -> None:
    incoming = [
        {"type": "at", "data": {"qq": "222", "name": "bot"}},
        {"type": "text", "data": {"text": " hi "}},
        {"type": "image", "data": {"url": "https://example.test/a.png"}},
        {"type": "at", "data": {"qq": "all"}},
        {"type": "reply", "data": {"id": "9"}},
        {"type": "face", "data": {"id": "123"}},
        {"type": "record", "data": {"file": "file://voice"}},
        {"type": "json", "data": {"raw": "{}"}},
    ]
    parts = segments_from_onebot(incoming)
    assert parts[0] == At("222", "bot")
    assert parts[1] == Plain(" hi ")
    assert isinstance(parts[2], Image)
    assert parts[2].file == "https://example.test/a.png"
    assert isinstance(parts[3], AtAll)
    assert parts[4] == Reply("9")
    assert parts[5] == Face("123")
    assert parts[6] == Record("file://voice")
    assert isinstance(parts[7], Unknown)
    assert parts[7].kind == "json"
    encoded = segments_to_onebot(parts)
    assert encoded[0] == {"type": "at", "data": {"qq": "222", "name": "bot"}}
    assert encoded[1] == {"type": "text", "data": {"text": " hi "}}
    assert encoded[2] == {"type": "image", "data": {"file": "https://example.test/a.png"}}
    assert encoded[3] == {"type": "at", "data": {"qq": "all"}}


def test_string_message_becomes_plain() -> None:
    assert segments_from_onebot("/help") == [Plain("/help")]
    assert segments_from_onebot([]) == []
    assert segments_from_onebot([], fallback="/help") == [Plain("/help")]


def test_env_secret_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "secret")
    assert resolve_secret("ENV:ONEBOT_ACCESS_TOKEN") == "secret"
    assert resolve_secret("plain") == "plain"
    monkeypatch.delenv("ONEBOT_ACCESS_TOKEN", raising=False)
    assert resolve_secret("ENV:ONEBOT_ACCESS_TOKEN") == ""


def test_settings_require_a_transport() -> None:
    with pytest.raises(ConfigError, match="ws_reverse_host"):
        OneBotV11Platform(
            PlatformConfig(id="qq", type="onebot_v11"),
            PlatformContext(sink=_Sink(), request_shutdown=lambda: None),
        )


def test_chain_outline_uses_converted_segments() -> None:
    chain = MessageChain(segments_from_onebot([{"type": "text", "data": {"text": "hi"}}]))
    assert chain.outline() == "hi"


def test_missing_env_is_empty() -> None:
    assert "VERTER_MISSING_ONEBOT_TOKEN" not in os.environ
    assert resolve_secret("ENV:VERTER_MISSING_ONEBOT_TOKEN") == ""
