"""Every LayoutView suggestionbox defines must respect Discord's component
limits (see `corridor/ui_limits.py` for the limits themselves and why
discord.py alone won't catch a violation).

Mirrors `corridor/tests/test_discord_ui_limits.py`/`toolbox/tests/
test_discord_ui_limits.py`: a hand-maintained factory registry cross-
checked against every `discord.ui.LayoutView` subclass actually declared
in `agent_access_panel.py`, so a new one added without a factory fails the
completeness test instead of silently going unchecked.
"""

from __future__ import annotations

import inspect
import unittest

import discord

from corridor import ui_limits

from ..adapters import agent_access_panel

VIEW_FACTORIES: dict[type, list[object]] = {
    agent_access_panel.AgentAccessView: [
        agent_access_panel.AgentAccessView([], 0, owner_id=1, enabled={}),
        agent_access_panel.AgentAccessView(["architect"], 0, owner_id=1, enabled={}),
        agent_access_panel.AgentAccessView(
            ["architect"], 0, owner_id=1, enabled={"architect": True}
        ),
        agent_access_panel.AgentAccessView(
            [f"agent{i}" for i in range(agent_access_panel.PAGE_SIZE)],
            0,
            owner_id=1,
            enabled={},
        ),
    ],
}


def _discovered_subclasses(base: type) -> set[type]:
    return {
        member
        for _, member in inspect.getmembers(agent_access_panel, inspect.isclass)
        if member.__module__ == agent_access_panel.__name__ and issubclass(member, base)
    }


class TestFactoryRegistryIsComplete(unittest.TestCase):
    """Fails loudly if a new View is added without a factory above."""

    def test_every_view_in_agent_access_panel_has_a_factory(self) -> None:
        discovered = _discovered_subclasses(discord.ui.LayoutView)
        registered = set(VIEW_FACTORIES)
        missing = discovered - registered
        self.assertFalse(
            missing,
            f"No ui_limits factory registered for: {[c.__name__ for c in missing]}. "
            "Add an entry to VIEW_FACTORIES in test_discord_ui_limits.py so its "
            "component limits get checked.",
        )


class TestViewsRespectDiscordLimits(unittest.TestCase):
    def test_every_registered_view_instance_passes(self) -> None:
        for view_type, instances in VIEW_FACTORIES.items():
            for instance in instances:
                with self.subTest(view=view_type.__name__):
                    violations = ui_limits.check_ui_tree(instance)
                    self.assertEqual(
                        violations,
                        [],
                        f"{view_type.__name__} violates Discord component limits:\n"
                        + ui_limits.format_violations(violations),
                    )


if __name__ == "__main__":
    unittest.main()
