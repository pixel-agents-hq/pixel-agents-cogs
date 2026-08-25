"""Every LayoutView toolbox defines must respect Discord's component
limits (see `corridor/ui_limits.py` for the limits themselves and why
discord.py alone won't catch a violation).

Mirrors `corridor/tests/test_discord_ui_limits.py`: a hand-maintained
factory registry cross-checked against every `discord.ui.LayoutView`
subclass actually declared in `tool_panel.py`, so a new one added without a
factory fails the completeness test instead of silently going unchecked.
"""

from __future__ import annotations

import inspect
import unittest

import discord

from corridor import ui_limits

from ..adapters import tool_panel
from ..adapters.tool_candidates import CandidateCommand


def _candidate(**overrides: object) -> CandidateCommand:
    defaults: dict[str, object] = dict(
        qualified_name="toolbox greet",
        tool_name="toolbox_greet",
        short_doc="Greet someone by name.",
        already_decorated=False,
        selected=False,
    )
    defaults.update(overrides)
    return CandidateCommand(**defaults)  # type: ignore[arg-type]


# --- View factories: one entry per `discord.ui.LayoutView` subclass. Cover
# every conditionally rendered row shape (undecorated/decorated/selected,
# with/without nav buttons enabled). ---
VIEW_FACTORIES: dict[type, list[object]] = {
    tool_panel.ToolSelectionView: [
        tool_panel.ToolSelectionView([], 0, owner_id=1, enabled_defaults={}),
        tool_panel.ToolSelectionView([_candidate()], 0, owner_id=1, enabled_defaults={}),
        tool_panel.ToolSelectionView(
            [_candidate(selected=True)], 0, owner_id=1, enabled_defaults={}
        ),
        tool_panel.ToolSelectionView(
            [_candidate(selected=True)],
            0,
            owner_id=1,
            enabled_defaults={"toolbox_greet": False},
        ),
        tool_panel.ToolSelectionView(
            [_candidate(already_decorated=True)], 0, owner_id=1, enabled_defaults={}
        ),
        tool_panel.ToolSelectionView(
            [
                _candidate(qualified_name=f"toolbox cmd{i}", tool_name=f"toolbox_cmd{i}")
                for i in range(tool_panel.PAGE_SIZE)
            ],
            0,
            owner_id=1,
            enabled_defaults={},
        ),
    ],
    tool_panel.ToolGuildOverrideView: [
        tool_panel.ToolGuildOverrideView([], 0, guild_id=1, admin_id=1, defaults={}, overrides={}),
        tool_panel.ToolGuildOverrideView(
            ["a_tool", "b_tool"],
            0,
            guild_id=1,
            admin_id=1,
            defaults={"a_tool": False},
            overrides={"b_tool": False},
        ),
        tool_panel.ToolGuildOverrideView(
            [f"tool_{i}" for i in range(tool_panel.PAGE_SIZE)],
            0,
            guild_id=1,
            admin_id=1,
            defaults={},
            overrides={},
        ),
    ],
}


def _discovered_subclasses(base: type) -> set[type]:
    return {
        member
        for _, member in inspect.getmembers(tool_panel, inspect.isclass)
        if member.__module__ == tool_panel.__name__ and issubclass(member, base)
    }


class TestFactoryRegistryIsComplete(unittest.TestCase):
    """Fails loudly if a new View is added without a factory above."""

    def test_every_view_in_tool_panel_has_a_factory(self) -> None:
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
