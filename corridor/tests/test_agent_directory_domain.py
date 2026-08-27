"""card_with_url is the one place corridor mutates a protobuf AgentCard --
see docs/agent-directory-design.md on why this is CopyFrom + rebuild the
repeated field, not a dataclasses.replace-style call."""

from __future__ import annotations

import unittest

from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from ..domain import card_with_url


def _card() -> AgentCard:
    return AgentCard(
        name="architect",
        description="desc",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url="http://placeholder/", protocol_binding=TransportProtocol.JSONRPC.value
            )
        ],
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )


class TestCardWithUrl(unittest.TestCase):
    def test_replaces_the_url_and_keeps_everything_else(self) -> None:
        original = _card()

        rewritten = card_with_url(original, "http://127.0.0.1:8931/architect/")

        self.assertEqual(len(rewritten.supported_interfaces), 1)
        self.assertEqual(rewritten.supported_interfaces[0].url, "http://127.0.0.1:8931/architect/")
        self.assertEqual(rewritten.name, "architect")
        self.assertEqual(rewritten.description, "desc")

    def test_does_not_mutate_the_original(self) -> None:
        original = _card()

        card_with_url(original, "http://127.0.0.1:8931/architect/")

        self.assertEqual(original.supported_interfaces[0].url, "http://placeholder/")

    def test_sets_icon_url_when_given(self) -> None:
        rewritten = card_with_url(
            _card(),
            "http://127.0.0.1:8931/architect/",
            icon_url="http://127.0.0.1:8931/architect/avatar.png",
        )

        self.assertEqual(rewritten.icon_url, "http://127.0.0.1:8931/architect/avatar.png")

    def test_icon_url_defaults_to_empty_when_not_given(self) -> None:
        rewritten = card_with_url(_card(), "http://127.0.0.1:8931/architect/")

        self.assertEqual(rewritten.icon_url, "")


if __name__ == "__main__":
    unittest.main()
