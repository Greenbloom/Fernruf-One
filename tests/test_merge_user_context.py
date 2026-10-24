from __future__ import annotations

from fernruf.voice.processors.merge_user_context import merge_consecutive_user_messages


def test_merge_dedupes_duplicate_user_messages() -> None:
    msgs = [
        {"role": "user", "content": "Hi"},
        {"role": "user", "content": "Hi"},
        {"role": "user", "content": "Bye."},
    ]
    merged = merge_consecutive_user_messages(msgs)
    assert [m["content"] for m in merged if m["role"] == "user"] == ["Hi Bye."]


def test_merge_consecutive_user_messages() -> None:
    msgs = [
        {"role": "assistant", "content": "Hallo"},
        {"role": "user", "content": "Bye."},
        {"role": "user", "content": "Hi"},
        {"role": "user", "content": "Ich habe Hi gesagt."},
    ]
    merged = merge_consecutive_user_messages(msgs)
    users = [m for m in merged if m["role"] == "user"]
    assert len(users) == 1
    assert "Hi" in users[0]["content"]
    assert "gesagt" in users[0]["content"]
