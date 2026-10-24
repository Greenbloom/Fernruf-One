from __future__ import annotations

import pytest

from fernruf.settings import Settings


def test_settings_rejects_garbage_quiet_hours() -> None:
    with pytest.raises(ValueError):
        Settings(quiet_hours="bad", _env_file=None)


def test_settings_empty_disables_quiet_hours() -> None:
    s = Settings(quiet_hours="", quiet_hours_allow_off=True, _env_file=None)
    assert not s.quiet_hours_spec.enabled
