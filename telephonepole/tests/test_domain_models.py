"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import ThirdPartyMcpServer


def test_third_party_mcp_server_holds_its_fields() -> None:
    server = ThirdPartyMcpServer(name="freecad", base_url="http://freecad-mcp:8765/mcp")

    assert server.name == "freecad"
    assert server.base_url == "http://freecad-mcp:8765/mcp"


def test_third_party_mcp_server_is_frozen() -> None:
    server = ThirdPartyMcpServer(name="freecad", base_url="http://freecad-mcp:8765/mcp")

    with pytest.raises(FrozenInstanceError):
        server.base_url = "http://other:8765/mcp"  # type: ignore[misc]
