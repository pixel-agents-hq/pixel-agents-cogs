"""Unit tests for corridor/requirement_hint.py and the load error setup() raises."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from redbot.core.errors import CogLoadError

import corridor
from corridor.requirement_hint import is_pin_break, pin_break_message

REQUIREMENT = (
    "pixel-agents-deps @ git+https://github.com/pixel-agents-hq/pixel-agents-cogs"
    "@pixel-agents-deps-v1#subdirectory=requirements"
)


def _sentinel_error() -> ImportError:
    return ImportError(
        "cannot import name 'sentinel' from 'typing_extensions' (/env/typing_extensions.py)",
        name="typing_extensions",
    )


class TestIsPinBreak(unittest.TestCase):
    def test_typing_extensions_sentinel_is_a_pin_break(self) -> None:
        self.assertTrue(is_pin_break(_sentinel_error()))

    def test_module_parsed_from_the_message_when_name_is_unset(self) -> None:
        exc = ImportError("cannot import name 'Sentinel' from 'typing_extensions' (/x.py)")

        self.assertTrue(is_pin_break(exc))

    def test_a_break_deeper_in_the_chain_is_found(self) -> None:
        try:
            try:
                raise _sentinel_error()
            except ImportError as inner:
                raise RuntimeError("wrapped") from inner
        except RuntimeError as outer:
            self.assertTrue(is_pin_break(outer))

    def test_pydantic_core_mismatch_is_a_pin_break(self) -> None:
        exc = SystemError(
            "The installed pydantic-core version (2.33.2) is incompatible with the "
            "current pydantic version, which requires 2.41.5."
        )

        self.assertTrue(is_pin_break(exc))

    def test_a_missing_module_is_not_a_pin_break(self) -> None:
        self.assertFalse(is_pin_break(ModuleNotFoundError("No module named 'a2a'", name="a2a")))

    def test_an_unrelated_import_error_is_not_a_pin_break(self) -> None:
        exc = ImportError("cannot import name 'Corridor' from 'corridor.corridor'")

        self.assertFalse(is_pin_break(exc))


class TestPinBreakMessage(unittest.TestCase):
    def test_names_the_pipinstall_fix_with_the_requirement(self) -> None:
        message = pin_break_message(_sentinel_error(), [REQUIREMENT])

        assert message is not None
        self.assertIn("cannot import name 'sentinel'", message)
        self.assertIn(f'[p]pipinstall "{REQUIREMENT}"', message)
        self.assertIn("restart the bot", message)

    def test_names_the_package_without_a_requirement(self) -> None:
        message = pin_break_message(_sentinel_error(), [])

        assert message is not None
        self.assertIn("[p]pipinstall pixel-agents-deps,", message)

    def test_other_errors_get_no_message(self) -> None:
        self.assertIsNone(pin_break_message(ValueError("nope"), [REQUIREMENT]))

    def test_corridor_reads_its_own_requirement(self) -> None:
        self.assertEqual(corridor._REQUIREMENTS, (REQUIREMENT,))


class _BrokenModule(types.ModuleType):
    def __init__(self, error: BaseException) -> None:
        super().__init__("corridor.corridor")
        self._error = error

    def __getattr__(self, name: str) -> object:
        raise self._error


class TestSetup(unittest.IsolatedAsyncioTestCase):
    async def _setup_with(self, error: BaseException) -> None:
        with patch.dict(sys.modules, {"corridor.corridor": _BrokenModule(error)}):
            await corridor.setup(AsyncMock())

    async def test_a_pin_break_becomes_a_load_error_naming_the_fix(self) -> None:
        with self.assertRaises(CogLoadError) as raised:
            await self._setup_with(_sentinel_error())

        self.assertIn("[p]pipinstall", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, ImportError)

    async def test_other_import_errors_propagate_unchanged(self) -> None:
        error = ModuleNotFoundError("No module named 'a2a'", name="a2a")

        with self.assertRaises(ModuleNotFoundError):
            await self._setup_with(error)
