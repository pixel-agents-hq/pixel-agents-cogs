"""Unit tests for the pure component-limit checker in `corridor/ui_limits.py`.

These use plain `types.SimpleNamespace` stand-ins instead of any cog's
discord stub, so they verify the checker's own logic in isolation from
either cog's test infrastructure.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from corridor import ui_limits


def _modal(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {"title": "OK", "children": [], "add_item": lambda item: None}
    values.update(overrides)
    return SimpleNamespace(**values)


def _text_input(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "label": "Label",
        "placeholder": "",
        "required": True,
        "min_length": None,
        "max_length": 100,
        "default": "",
        "custom_id": "ti",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _button(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "label": "Click",
        "style": "primary",
        "disabled": False,
        "custom_id": "btn",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _label(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "text": "Label text",
        "description": "",
        "component": SimpleNamespace(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _select(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "placeholder": "Pick one",
        "options": [],
        "custom_id": "sel",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestComponentDispatch(unittest.TestCase):
    def test_text_input_is_checked_as_text_input_not_select(self) -> None:
        # TextInput has `placeholder` like Select does; make sure the >45
        # label limit (TextInput) fires rather than the >150 one (Select).
        violations = ui_limits.check_component(_text_input(label="x" * 46))
        self.assertEqual([v.attribute for v in violations], ["label"])

    def test_button_is_not_misread_as_text_input(self) -> None:
        violations = ui_limits.check_component(_button(label="x" * 81))
        self.assertEqual(
            [(v.component_type, v.attribute) for v in violations], [("Button", "label")]
        )

    def test_unrecognized_component_yields_no_violations(self) -> None:
        self.assertEqual(ui_limits.check_component(SimpleNamespace()), [])

    def test_bare_magicmock_is_declined_not_misdispatched(self) -> None:
        # hasattr() is always True on a MagicMock, which would otherwise
        # misdispatch it into e.g. check_text_input and silently read 0
        # off its auto `__len__` -- a false "no violations" rather than an
        # honest "this stub can't be checked".
        from unittest.mock import MagicMock

        self.assertEqual(ui_limits.check_component(MagicMock()), [])


class TestModalLimits(unittest.TestCase):
    def test_title_within_limit_passes(self) -> None:
        self.assertEqual(ui_limits.check_modal(_modal(title="x" * 45)), [])

    def test_title_over_limit_fails(self) -> None:
        violations = ui_limits.check_modal(_modal(title="x" * 46))
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].attribute, "title")
        self.assertEqual(violations[0].limit, 45)
        self.assertEqual(violations[0].actual, 46)

    def test_custom_id_over_limit_fails(self) -> None:
        violations = ui_limits.check_modal(_modal(custom_id="x" * 101))
        self.assertEqual([v.attribute for v in violations], ["custom_id"])


class TestTextInputLimits(unittest.TestCase):
    def test_label_over_45_fails(self) -> None:
        # Exactly the bug this suite exists to catch: a modal TextInput
        # label at 48 characters made send_modal() raise, unacknowledged.
        violations = ui_limits.check_text_input(
            _text_input(label="Custom icon URL (used when icon source = custom)")
        )
        self.assertEqual([v.attribute for v in violations], ["label"])

    def test_label_at_45_passes(self) -> None:
        self.assertEqual(ui_limits.check_text_input(_text_input(label="x" * 45)), [])

    def test_placeholder_over_100_fails(self) -> None:
        violations = ui_limits.check_text_input(_text_input(placeholder="x" * 101))
        self.assertEqual([v.attribute for v in violations], ["placeholder"])

    def test_default_over_4000_fails(self) -> None:
        violations = ui_limits.check_text_input(_text_input(default="x" * 4001))
        self.assertEqual([v.attribute for v in violations], ["default"])

    def test_max_length_over_4000_fails(self) -> None:
        violations = ui_limits.check_text_input(_text_input(max_length=4001))
        self.assertEqual([v.attribute for v in violations], ["max_length"])

    def test_min_length_over_4000_fails(self) -> None:
        violations = ui_limits.check_text_input(_text_input(min_length=4001))
        self.assertEqual([v.attribute for v in violations], ["min_length"])

    def test_custom_id_over_100_fails(self) -> None:
        violations = ui_limits.check_text_input(_text_input(custom_id="x" * 101))
        self.assertEqual([v.attribute for v in violations], ["custom_id"])


class TestButtonLimits(unittest.TestCase):
    def test_label_at_80_passes(self) -> None:
        self.assertEqual(ui_limits.check_button(_button(label="x" * 80)), [])

    def test_label_over_80_fails(self) -> None:
        violations = ui_limits.check_button(_button(label="x" * 81))
        self.assertEqual([v.attribute for v in violations], ["label"])

    def test_custom_id_over_100_fails(self) -> None:
        violations = ui_limits.check_button(_button(custom_id="x" * 101))
        self.assertEqual([v.attribute for v in violations], ["custom_id"])


class TestSelectLimits(unittest.TestCase):
    def test_placeholder_at_150_passes(self) -> None:
        self.assertEqual(ui_limits.check_select(_select(placeholder="x" * 150)), [])

    def test_placeholder_over_150_fails(self) -> None:
        violations = ui_limits.check_select(_select(placeholder="x" * 151))
        self.assertEqual([v.attribute for v in violations], ["placeholder"])

    def test_26_options_fails(self) -> None:
        violations = ui_limits.check_select(_select(options=list(range(26))))
        self.assertEqual([v.attribute for v in violations], ["options"])

    def test_25_options_passes(self) -> None:
        self.assertEqual(ui_limits.check_select(_select(options=list(range(25)))), [])

    def test_role_select_without_options_attribute_only_checks_placeholder(self) -> None:
        # RoleSelect/UserSelect/etc. have no `.options` at all -- absence
        # must not be misread as "0 options exceeds nothing" nor crash.
        role_select = SimpleNamespace(placeholder="x" * 151, min_values=0, max_values=25)
        violations = ui_limits.check_select(role_select)
        self.assertEqual([v.attribute for v in violations], ["placeholder"])


class TestLabelLimits(unittest.TestCase):
    """discord.ui.Label is the Components V2 replacement for the deprecated
    TextInput(label=...) pattern -- floorplan uses it, corridor doesn't."""

    def test_text_at_45_passes(self) -> None:
        self.assertEqual(ui_limits.check_label(_label(text="x" * 45)), [])

    def test_text_over_45_fails(self) -> None:
        violations = ui_limits.check_label(_label(text="x" * 46))
        self.assertEqual([v.attribute for v in violations], ["text"])

    def test_description_over_100_fails(self) -> None:
        violations = ui_limits.check_label(_label(description="x" * 101))
        self.assertEqual([v.attribute for v in violations], ["description"])

    def test_label_is_not_misread_as_modal_or_button(self) -> None:
        # Label has no `title`/`add_item` (not a Modal) and no `style`
        # (not a Button), so dispatch must land on check_label.
        violations = ui_limits.check_component(_label(text="x" * 46))
        self.assertEqual([v.component_type for v in violations], ["Label"])


class TestTreeWalking(unittest.TestCase):
    def test_walks_into_labels_wrapped_component(self) -> None:
        bad_input = _text_input(placeholder="x" * 101)
        label = _label(component=bad_input)

        violations = ui_limits.check_ui_tree(label)

        self.assertEqual(
            [(v.component_type, v.attribute) for v in violations],
            [("TextInput", "placeholder")],
        )

    def test_walks_into_sections_accessory(self) -> None:
        bad_button = _button(label="x" * 81)
        section = SimpleNamespace(children=[], accessory=bad_button)

        violations = ui_limits.check_ui_tree(section)

        self.assertEqual([v.component_type for v in violations], ["Button"])

    def test_finds_violation_nested_inside_container_and_action_row(self) -> None:
        bad_button = _button(label="x" * 81)
        action_row = SimpleNamespace(children=[bad_button])
        container = SimpleNamespace(children=[action_row])
        view = SimpleNamespace(children=[container])

        violations = ui_limits.check_ui_tree(view)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].component_type, "Button")

    def test_cycle_does_not_infinite_loop(self) -> None:
        node = SimpleNamespace(children=[])
        node.children.append(node)

        violations = ui_limits.check_ui_tree(node)

        self.assertEqual(violations, [])

    def test_check_ui_trees_aggregates_across_roots(self) -> None:
        first = _modal(title="x" * 46)
        second = _button(label="x" * 81)

        violations = ui_limits.check_ui_trees([first, second])

        self.assertEqual(len(violations), 2)


if __name__ == "__main__":
    unittest.main()
