"""Discord message-component limit checks, independent of any one cog.

Both `corridor` and `floorplan` build Components V2 UIs (Modals, Buttons,
Selects, TextInputs) by hand, and each cog stubs `discord.ui` differently for
its own unit tests (see the collision warning in `corridor/testing.py`). A
regression like a too-long modal label -- which discord.py does not validate
client-side, so it only surfaces as an opaque `HTTPException`/interaction
timeout at runtime -- can slip past both stub suites unless something checks
the actual limits.

This module is that something. It is pure and duck-typed: it inspects
whatever attributes a component object happens to expose (`label`, `title`,
`placeholder`, `custom_id`, `options`, `min_length`/`max_length`, ...) and
works equally well against real `discord.py` objects or a cog's local test
stubs, as long as the stub's attribute names match discord.py's. It imports
nothing from `discord` or either cog, so it carries no risk of joining the
two cogs' incompatible stub sets together.

Limits below are Discord's documented interaction-component limits as
implemented by discord.py 2.7 (`discord/ui/modal.py`, `discord/ui/text_input.py`,
`discord/ui/button.py`, `discord/ui/select.py`, `discord/components.py`):

- Modal.title: 45 chars: https://discord.com/developers/docs/interactions/message-components
- Modal.custom_id / Button.custom_id / Select.custom_id / TextInput.custom_id: 100 chars
- TextInput.label: 45 chars
- TextInput.placeholder: 100 chars
- TextInput.value/default length and min_length/max_length bound: 4000 chars
- Button.label: 80 chars
- Select.placeholder: 150 chars
- Select.options: 25 entries
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import NonCallableMock

MODAL_TITLE_MAX_LENGTH = 45
MODAL_CUSTOM_ID_MAX_LENGTH = 100

TEXT_INPUT_LABEL_MAX_LENGTH = 45
TEXT_INPUT_PLACEHOLDER_MAX_LENGTH = 100
TEXT_INPUT_CUSTOM_ID_MAX_LENGTH = 100
TEXT_INPUT_VALUE_MAX_LENGTH = 4000
TEXT_INPUT_LENGTH_BOUND_MAX = 4000  # cap for both min_length and max_length

BUTTON_LABEL_MAX_LENGTH = 80
BUTTON_CUSTOM_ID_MAX_LENGTH = 100

SELECT_PLACEHOLDER_MAX_LENGTH = 150
SELECT_CUSTOM_ID_MAX_LENGTH = 100
SELECT_MAX_OPTIONS = 25

# Real Discord UI trees top out at a few dozen components. This is a
# generous ceiling on `iter_ui_tree`'s walk, not a Discord-documented limit
# -- it exists so a runaway traversal (an accidental cycle, or a test stub
# that auto-vivifies a new child object on every attribute access, e.g. a
# bare `unittest.mock.MagicMock` standing in for a nested component) fails
# fast with a clear error instead of allocating objects until the process
# OOMs. See git history: a bare-MagicMock `Section` stub caused exactly
# this and took down shared test infrastructure.
MAX_TREE_NODES = 500

# discord.ui.Label wraps a single input in Components V2 -- it's the
# replacement for the deprecated TextInput(label=...)/Select(placeholder=...)
# pattern, and carries the same 45/100-char limits under different names.
LABEL_TEXT_MAX_LENGTH = 45
LABEL_DESCRIPTION_MAX_LENGTH = 100

# A handful of components nest exactly one other component through a
# singular attribute rather than the `.children` list: Section.accessory
# and Label.component. Both need to be walked for the tree checks below to
# see what's actually inside them.
_SINGLE_CHILD_ATTRS = ("accessory", "component")


@dataclass(frozen=True)
class ComponentViolation:
    """One Discord component limit exceeded by one component instance."""

    component_type: str
    identifier: str
    attribute: str
    limit: int
    actual: int

    def __str__(self) -> str:
        return (
            f"{self.component_type} {self.identifier!r}: `{self.attribute}` is "
            f"{self.actual}, which exceeds Discord's limit of {self.limit}"
        )


def _text_len(value: Any) -> int:
    return len(value) if value else 0


def _identify(component: Any, *candidates: str) -> str:
    """Best-effort human-readable label for a violation message."""
    for attr in candidates:
        value = getattr(component, attr, None)
        if value:
            return str(value)
    return type(component).__name__


def _is_text_input(component: Any) -> bool:
    # TextInput is the only component with both `required` and `max_length`.
    return hasattr(component, "required") and hasattr(component, "max_length")


def _is_select(component: Any) -> bool:
    # Every select flavor (Select, RoleSelect, UserSelect, ChannelSelect,
    # MentionableSelect) has `min_values`; plain Select also has `options`.
    return hasattr(component, "min_values") or hasattr(component, "options")


def _is_button(component: Any) -> bool:
    return (
        hasattr(component, "style")
        and hasattr(component, "label")
        and not _is_text_input(component)
    )


def _is_modal(component: Any) -> bool:
    return (
        hasattr(component, "title")
        and hasattr(component, "children")
        and hasattr(component, "add_item")
    )


def _is_label(component: Any) -> bool:
    return (
        hasattr(component, "text")
        and hasattr(component, "component")
        and hasattr(component, "description")
    )


def check_modal(modal: Any) -> list[ComponentViolation]:
    violations: list[ComponentViolation] = []
    identifier = _identify(modal, "title")
    title = getattr(modal, "title", "") or ""
    if len(title) > MODAL_TITLE_MAX_LENGTH:
        violations.append(
            ComponentViolation("Modal", identifier, "title", MODAL_TITLE_MAX_LENGTH, len(title))
        )
    custom_id = getattr(modal, "custom_id", None)
    if custom_id and len(custom_id) > MODAL_CUSTOM_ID_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "Modal", identifier, "custom_id", MODAL_CUSTOM_ID_MAX_LENGTH, len(custom_id)
            )
        )
    return violations


def check_text_input(text_input: Any) -> list[ComponentViolation]:
    violations: list[ComponentViolation] = []
    identifier = _identify(text_input, "label", "placeholder")

    label = getattr(text_input, "label", "") or ""
    if len(label) > TEXT_INPUT_LABEL_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "TextInput", identifier, "label", TEXT_INPUT_LABEL_MAX_LENGTH, len(label)
            )
        )

    placeholder = getattr(text_input, "placeholder", "") or ""
    if len(placeholder) > TEXT_INPUT_PLACEHOLDER_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "TextInput",
                identifier,
                "placeholder",
                TEXT_INPUT_PLACEHOLDER_MAX_LENGTH,
                len(placeholder),
            )
        )

    custom_id = getattr(text_input, "custom_id", None)
    if custom_id and len(custom_id) > TEXT_INPUT_CUSTOM_ID_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "TextInput",
                identifier,
                "custom_id",
                TEXT_INPUT_CUSTOM_ID_MAX_LENGTH,
                len(custom_id),
            )
        )

    default_value = getattr(text_input, "default", None)
    if default_value is None:
        default_value = getattr(text_input, "value", None)
    if default_value and len(default_value) > TEXT_INPUT_VALUE_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "TextInput",
                identifier,
                "default",
                TEXT_INPUT_VALUE_MAX_LENGTH,
                len(default_value),
            )
        )

    for attr in ("min_length", "max_length"):
        bound = getattr(text_input, attr, None)
        if bound is not None and bound > TEXT_INPUT_LENGTH_BOUND_MAX:
            violations.append(
                ComponentViolation(
                    "TextInput", identifier, attr, TEXT_INPUT_LENGTH_BOUND_MAX, bound
                )
            )

    return violations


def check_button(button: Any) -> list[ComponentViolation]:
    violations: list[ComponentViolation] = []
    identifier = _identify(button, "label")

    label = getattr(button, "label", "") or ""
    if len(label) > BUTTON_LABEL_MAX_LENGTH:
        violations.append(
            ComponentViolation("Button", identifier, "label", BUTTON_LABEL_MAX_LENGTH, len(label))
        )

    custom_id = getattr(button, "custom_id", None)
    if custom_id and len(custom_id) > BUTTON_CUSTOM_ID_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "Button", identifier, "custom_id", BUTTON_CUSTOM_ID_MAX_LENGTH, len(custom_id)
            )
        )

    return violations


def check_select(select: Any) -> list[ComponentViolation]:
    violations: list[ComponentViolation] = []
    identifier = _identify(select, "placeholder")

    placeholder = getattr(select, "placeholder", "") or ""
    if len(placeholder) > SELECT_PLACEHOLDER_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "Select",
                identifier,
                "placeholder",
                SELECT_PLACEHOLDER_MAX_LENGTH,
                len(placeholder),
            )
        )

    options = getattr(select, "options", None)
    if options is not None and len(options) > SELECT_MAX_OPTIONS:
        violations.append(
            ComponentViolation("Select", identifier, "options", SELECT_MAX_OPTIONS, len(options))
        )

    custom_id = getattr(select, "custom_id", None)
    if custom_id and len(custom_id) > SELECT_CUSTOM_ID_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "Select", identifier, "custom_id", SELECT_CUSTOM_ID_MAX_LENGTH, len(custom_id)
            )
        )

    return violations


def check_label(label: Any) -> list[ComponentViolation]:
    """Check a `discord.ui.Label` -- the Components V2 replacement for
    TextInput(label=...): it wraps an input and carries the label text and
    description that used to live on the input itself."""
    violations: list[ComponentViolation] = []
    identifier = _identify(label, "text")

    text = getattr(label, "text", "") or ""
    if len(text) > LABEL_TEXT_MAX_LENGTH:
        violations.append(
            ComponentViolation("Label", identifier, "text", LABEL_TEXT_MAX_LENGTH, len(text))
        )

    description = getattr(label, "description", "") or ""
    if len(description) > LABEL_DESCRIPTION_MAX_LENGTH:
        violations.append(
            ComponentViolation(
                "Label",
                identifier,
                "description",
                LABEL_DESCRIPTION_MAX_LENGTH,
                len(description),
            )
        )

    return violations


def check_component(component: Any) -> list[ComponentViolation]:
    """Dispatch a single component to its limit checker by duck type.

    Order matters: TextInput and Select both expose `placeholder`, so
    TextInput (which additionally has `required`/`max_length`) is checked
    first; Select is checked before Button since neither has `style`.
    """
    if isinstance(component, NonCallableMock):
        # `hasattr()` is always True on a bare Mock/MagicMock -- it
        # auto-creates whatever attribute you ask for. Duck-typing a mock
        # would misdispatch it into some check and then silently read "0"
        # off its auto-generated `__len__`, reporting false confidence
        # instead of an honest "can't check this". A cog that wants a
        # placeholder component genuinely validated needs a real (if
        # minimal) stub class, not a bare Mock standing in for it.
        return []
    if _is_text_input(component):
        return check_text_input(component)
    if _is_select(component):
        return check_select(component)
    if _is_button(component):
        return check_button(component)
    if _is_label(component):
        return check_label(component)
    if _is_modal(component):
        return check_modal(component)
    return []


def iter_ui_tree(root: Any) -> Iterator[Any]:
    """Yield `root` and every component reachable through `.children`,
    `.accessory` (Section), or `.component` (Label).

    Raises `RuntimeError` if more than `MAX_TREE_NODES` are visited -- see
    that constant's docstring for why this ceiling exists.
    """
    seen: set[int] = set()
    stack = [root]
    while stack:
        if len(seen) > MAX_TREE_NODES:
            raise RuntimeError(
                f"iter_ui_tree visited more than {MAX_TREE_NODES} components while "
                f"walking a {type(root).__name__!r} tree -- this almost certainly means "
                "a cycle or a test stub that auto-vivifies a new child object on every "
                "attribute access (e.g. a bare `unittest.mock.MagicMock` standing in for "
                "a nested component), not a real Discord UI tree. Give the stub real "
                "`.children`/`.accessory`/`.component` attributes instead of a bare mock."
            )
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        children = getattr(node, "children", None)
        if children:
            stack.extend(children)
        for attr in _SINGLE_CHILD_ATTRS:
            child = getattr(node, attr, None)
            if child is not None:
                stack.append(child)


def check_ui_tree(root: Any) -> list[ComponentViolation]:
    """Walk a View/Modal/Container tree and collect every limit violation."""
    violations: list[ComponentViolation] = []
    for node in iter_ui_tree(root):
        violations.extend(check_component(node))
    return violations


def check_ui_trees(roots: Iterable[Any]) -> list[ComponentViolation]:
    violations: list[ComponentViolation] = []
    for root in roots:
        violations.extend(check_ui_tree(root))
    return violations


def format_violations(violations: Iterable[ComponentViolation]) -> str:
    return "\n".join(f"- {violation}" for violation in violations)
