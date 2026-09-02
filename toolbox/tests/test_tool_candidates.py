"""list_candidate_commands is fully testable without Red -- small stand-ins
for command-like objects, same style as test_tool_wrapping.py."""

from __future__ import annotations

import unittest

from corridor.domain import llm_tool

from ..adapters.tool_candidates import list_candidate_commands


class _StubCommand:
    def __init__(
        self,
        *,
        qualified_name: str,
        callback: object = None,
        hidden: bool = False,
        enabled: bool = True,
        allowed: bool = True,
        short_doc: str = "",
        raises: bool = False,
    ) -> None:
        self.qualified_name = qualified_name
        self.callback = callback
        self.hidden = hidden
        self.enabled = enabled
        self.allowed = allowed
        self.short_doc = short_doc
        self.raises = raises

    async def can_run(self, ctx: object) -> bool:
        if self.raises:
            raise RuntimeError("broken check")
        return self.allowed


@llm_tool(name="a_tool", description="Does a thing.")
async def _decorated_command(cog: object, ctx: object) -> None: ...


class TestListCandidateCommands(unittest.IsolatedAsyncioTestCase):
    async def test_a_plain_command_is_a_candidate(self) -> None:
        command = _StubCommand(qualified_name="toolbox greet", short_doc="Greet someone.")

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.qualified_name, "toolbox greet")
        self.assertEqual(candidate.tool_name, "toolbox_greet")
        self.assertEqual(candidate.short_doc, "Greet someone.")
        self.assertFalse(candidate.already_decorated)
        self.assertFalse(candidate.selected)

    async def test_a_hidden_command_is_excluded(self) -> None:
        command = _StubCommand(qualified_name="toolbox hidden", hidden=True)

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertEqual(candidates, [])

    async def test_a_disabled_command_is_excluded(self) -> None:
        command = _StubCommand(qualified_name="toolbox disabled", enabled=False)

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertEqual(candidates, [])

    async def test_a_command_the_invoker_cannot_run_is_excluded(self) -> None:
        command = _StubCommand(qualified_name="toolbox denied", allowed=False)

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertEqual(candidates, [])

    async def test_a_broken_can_run_check_excludes_the_command_without_raising(self) -> None:
        command = _StubCommand(qualified_name="toolbox broken", raises=True)

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertEqual(candidates, [])

    async def test_a_command_without_a_short_doc_gets_a_placeholder(self) -> None:
        command = _StubCommand(qualified_name="toolbox quiet", short_doc="")

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertEqual(candidates[0].short_doc, "(no description)")

    async def test_an_already_decorated_command_is_flagged(self) -> None:
        command = _StubCommand(qualified_name="toolbox decorated", callback=_decorated_command)

        candidates = await list_candidate_commands([command], ctx=object(), selected=frozenset())

        self.assertTrue(candidates[0].already_decorated)

    async def test_a_selected_command_is_flagged(self) -> None:
        command = _StubCommand(qualified_name="toolbox greet")

        candidates = await list_candidate_commands(
            [command], ctx=object(), selected=frozenset({"toolbox greet"})
        )

        self.assertTrue(candidates[0].selected)

    async def test_duplicate_qualified_names_are_deduped(self) -> None:
        command = _StubCommand(qualified_name="toolbox greet")

        candidates = await list_candidate_commands(
            [command, command], ctx=object(), selected=frozenset()
        )

        self.assertEqual(len(candidates), 1)

    async def test_candidates_are_sorted_by_qualified_name(self) -> None:
        commands = [
            _StubCommand(qualified_name="toolbox zeta"),
            _StubCommand(qualified_name="toolbox alpha"),
        ]

        candidates = await list_candidate_commands(commands, ctx=object(), selected=frozenset())

        self.assertEqual(
            [candidate.qualified_name for candidate in candidates],
            ["toolbox alpha", "toolbox zeta"],
        )


class TestSearchFiltering(unittest.IsolatedAsyncioTestCase):
    def _commands(self) -> list[_StubCommand]:
        return [
            _StubCommand(qualified_name="toolbox node install"),
            _StubCommand(qualified_name="toolbox node uninstall"),
            _StubCommand(qualified_name="toolbox tools guild"),
        ]

    async def test_no_search_returns_every_candidate(self) -> None:
        candidates = await list_candidate_commands(
            self._commands(), ctx=object(), selected=frozenset()
        )

        self.assertEqual(len(candidates), 3)

    async def test_search_filters_to_matching_names(self) -> None:
        candidates = await list_candidate_commands(
            self._commands(), ctx=object(), selected=frozenset(), search="node"
        )

        self.assertEqual(
            [candidate.qualified_name for candidate in candidates],
            ["toolbox node install", "toolbox node uninstall"],
        )

    async def test_search_is_case_insensitive(self) -> None:
        candidates = await list_candidate_commands(
            self._commands(), ctx=object(), selected=frozenset(), search="NODE"
        )

        self.assertEqual(len(candidates), 2)

    async def test_search_with_no_match_returns_no_candidates(self) -> None:
        candidates = await list_candidate_commands(
            self._commands(), ctx=object(), selected=frozenset(), search="nonexistent"
        )

        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
