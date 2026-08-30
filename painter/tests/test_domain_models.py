"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import GlobalSettings


def test_global_settings_holds_its_fields() -> None:
    settings = GlobalSettings(max_tool_calls=5, system_prompt="Be terse.", debug_logging=True)

    assert settings.max_tool_calls == 5
    assert settings.system_prompt == "Be terse."
    assert settings.debug_logging is True


def test_global_settings_is_frozen() -> None:
    settings = GlobalSettings(max_tool_calls=5, system_prompt="Be terse.", debug_logging=True)

    with pytest.raises(FrozenInstanceError):
        settings.max_tool_calls = 3  # type: ignore[misc]
