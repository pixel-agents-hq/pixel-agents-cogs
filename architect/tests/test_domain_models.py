"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from ..domain import GlobalSettings


def test_global_settings_holds_its_fields() -> None:
    settings = GlobalSettings(max_tool_calls=5, system_prompt="p", debug_logging=False)

    assert settings.max_tool_calls == 5
    assert settings.system_prompt == "p"
    assert settings.debug_logging is False
