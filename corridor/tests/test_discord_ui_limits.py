"""Every Modal/View corridor defines must respect Discord's component
limits (see `ui_limits.py` at the repo root for the limits themselves and
why discord.py alone won't catch a violation).

This file discovers every `discord.ui.Modal`/`discord.ui.LayoutView`
subclass declared in `corridor.adapters.settings_ui` and cross-checks that
against a hand-maintained factory registry below. If someone adds a new
Modal or View to that module without registering a factory here, the
completeness test fails loudly instead of silently skipping the new UI --
that's what makes this "automatic" for future additions: forgetting to
wire up a factory is itself a caught regression.
"""

from __future__ import annotations

import inspect
import unittest

import discord

import ui_limits

from ..adapters import settings_ui
from ..domain import (
    GuildSettings,
    IconPreference,
    IconSource,
    PermissionGroupDef,
    PermissionSettings,
    ReplyMode,
    ReplyPreferences,
)


def _settings(*, group_count: int = 2, footer_text: str | None = None) -> GuildSettings:
    groups = tuple(
        PermissionGroupDef(key=f"group_{i}", label=f"Group {i}") for i in range(group_count)
    )
    return GuildSettings(
        guild_id=1,
        reply=ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=True,
            footer_text=footer_text,
            icon=IconPreference(source=IconSource.BOT),
        ),
        permissions=PermissionSettings(groups=groups),
    )


# --- Modal factories: one entry per `discord.ui.Modal` subclass in
# settings_ui, each producing a representative instance to validate. ---
MODAL_FACTORIES: dict[type, list[object]] = {
    settings_ui.SharedSettingsModal: [
        settings_ui.SharedSettingsModal(_settings()),
        settings_ui.SharedSettingsModal(_settings(footer_text="x" * 200)),
    ],
    settings_ui.TierLabelsModal: [settings_ui.TierLabelsModal(_settings())],
    settings_ui.AddGroupModal: [settings_ui.AddGroupModal()],
    settings_ui.RenameGroupModal: [
        settings_ui.RenameGroupModal("keyholder", "Keyholder"),
        # A label at PermissionGroupDef's own max should still fit the modal.
        settings_ui.RenameGroupModal("group_1", "x" * 50),
    ],
}

# --- View factories: one entry per `discord.ui.LayoutView` subclass. Cover
# both the empty-groups and max-groups shapes so every conditionally
# rendered button (e.g. "Add group" disabled at MAX_PERMISSION_GROUPS) and
# every per-group row gets exercised. ---
VIEW_FACTORIES: dict[type, list[object]] = {
    settings_ui.SharedSettingsView: [
        settings_ui.SharedSettingsView(_settings(group_count=0)),
        settings_ui.SharedSettingsView(_settings(group_count=2)),
        settings_ui.SharedSettingsView(_settings(group_count=settings_ui.MAX_PERMISSION_GROUPS)),
    ],
}


def _discovered_subclasses(base: type) -> set[type]:
    return {
        member
        for _, member in inspect.getmembers(settings_ui, inspect.isclass)
        if member.__module__ == settings_ui.__name__ and issubclass(member, base)
    }


class TestFactoryRegistryIsComplete(unittest.TestCase):
    """Fails loudly if a new Modal/View is added without a factory above."""

    def test_every_modal_in_settings_ui_has_a_factory(self) -> None:
        discovered = _discovered_subclasses(discord.ui.Modal)
        registered = set(MODAL_FACTORIES)
        missing = discovered - registered
        self.assertFalse(
            missing,
            f"No ui_limits factory registered for: {[c.__name__ for c in missing]}. "
            "Add an entry to MODAL_FACTORIES in test_discord_ui_limits.py so its "
            "component limits get checked.",
        )

    def test_every_view_in_settings_ui_has_a_factory(self) -> None:
        discovered = _discovered_subclasses(discord.ui.LayoutView)
        registered = set(VIEW_FACTORIES)
        missing = discovered - registered
        self.assertFalse(
            missing,
            f"No ui_limits factory registered for: {[c.__name__ for c in missing]}. "
            "Add an entry to VIEW_FACTORIES in test_discord_ui_limits.py so its "
            "component limits get checked.",
        )


class TestModalsRespectDiscordLimits(unittest.TestCase):
    def test_every_registered_modal_instance_passes(self) -> None:
        for modal_type, instances in MODAL_FACTORIES.items():
            for instance in instances:
                with self.subTest(modal=modal_type.__name__):
                    violations = ui_limits.check_ui_tree(instance)
                    self.assertEqual(
                        violations,
                        [],
                        f"{modal_type.__name__} violates Discord component limits:\n"
                        + ui_limits.format_violations(violations),
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


class TestRegressionCaseFromTheOutage(unittest.TestCase):
    """The exact bug: a 48-char TextInput label made send_modal() raise an
    uncaught HTTPException, so Discord showed "didn't respond in time"."""

    def test_a_too_long_text_input_label_is_caught(self) -> None:
        too_long = discord.ui.TextInput(label="Custom icon URL (used when icon source = custom)")

        violations = ui_limits.check_text_input(too_long)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].attribute, "label")

    def test_the_shipped_label_is_within_the_limit(self) -> None:
        modal = settings_ui.SharedSettingsModal(_settings())

        violations = ui_limits.check_ui_tree(modal)

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
