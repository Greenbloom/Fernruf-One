"""What a person sees instead of a phone number."""

from fernruf.monitor.protocol import mask_e164


def test_mask_keeps_the_country_code_and_the_last_four_digits() -> None:
    assert mask_e164("+493023125123") == "+49 ••• ••• 5123"
    assert mask_e164("+14155552671") == "+1 ••• ••• 2671"
    assert mask_e164("+353871234567") == "+353 •• ••• 4567"
    assert mask_e164("03023125123") == "• ••• ••• 5123"
    assert mask_e164("+493023125123") != "+493023125123"
    assert mask_e164("12") == "••••"


def test_a_listen_yes_is_forgotten_when_the_call_ends() -> None:
    from fernruf.desktop.choices import listen_from_disk, listen_to_store

    assert listen_to_store(True) is False
    assert listen_from_disk(True) is False
