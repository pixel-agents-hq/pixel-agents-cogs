"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from ..domain import GlobalSettings


def test_global_settings_holds_its_fields() -> None:
    settings = GlobalSettings(
        max_tool_calls=5,
        system_prompt="p",
        a2a_host="127.0.0.1",
        a2a_port=8931,
        ws_host="127.0.0.1",
        ws_port=8932,
        debug_logging=False,
    )

    assert settings.max_tool_calls == 5
    assert settings.system_prompt == "p"
    assert settings.a2a_host == "127.0.0.1"
    assert settings.a2a_port == 8931
    assert settings.ws_host == "127.0.0.1"
    assert settings.ws_port == 8932
    assert settings.debug_logging is False
