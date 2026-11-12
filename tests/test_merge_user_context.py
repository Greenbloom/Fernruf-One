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


def test_a_later_hold_replaces_the_unanswered_user_line() -> None:
    msgs = [
        {"role": "user", "content": "Hallo."},
        {"role": "user", "content": "Warte, ich teste eben die Knöpfe oben rechts."},
    ]
    merged = merge_consecutive_user_messages(msgs)
    users = [m["content"] for m in merged if m["role"] == "user"]
    assert users == ["Warte, ich teste eben die Knöpfe oben rechts."]


def test_hold_request_does_not_swallow_the_next_user_turn() -> None:
    msgs = [
        {"role": "assistant", "content": "Funktionieren die Knöpfe?"},
        {"role": "user", "content": "Warte, ich teste eben die Knöpfe oben rechts."},
        {"role": "user", "content": "Ich habe noch eine Frage an dich."},
    ]
    merged = merge_consecutive_user_messages(msgs)
    users = [m["content"] for m in merged if m["role"] == "user"]
    assert users == ["Ich habe noch eine Frage an dich."]


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
