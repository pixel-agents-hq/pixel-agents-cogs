"""llm_tool/llm_tool_spec are fully testable without Red: they only
inspect the decorated callable's own signature, no discord.py/redbot
stubbing needed."""

from __future__ import annotations

import unittest
from typing import Annotated

from ..domain import llm_tool, llm_tool_spec


class TestLLMToolSpec(unittest.TestCase):
    def test_undecorated_function_has_no_spec(self) -> None:
        def plain(self: object, ctx: object) -> None: ...

        self.assertIsNone(llm_tool_spec(plain))

    def test_decorator_attaches_the_given_name_description_and_group(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.", required_group="employee")
        def command(self: object, ctx: object) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(spec.name, "a_tool")
        self.assertEqual(spec.description, "Does a thing.")
        self.assertEqual(spec.required_group, "employee")

    def test_required_group_defaults_to_none(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertIsNone(spec.required_group)

    def test_decorator_returns_the_original_callable_still_working(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object) -> str:
            return "ran"

        self.assertEqual(command(object(), object()), "ran")

    def test_no_extra_parameters_yields_an_empty_schema(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(spec.parameters, {"type": "object", "properties": {}, "required": []})

    def test_required_str_parameter(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object, name: str) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(spec.parameters["properties"], {"name": {"type": "string"}})
        self.assertEqual(spec.parameters["required"], ["name"])

    def test_optional_str_or_none_parameter_is_not_required(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object, timezone: str | None = None) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(spec.parameters["properties"], {"timezone": {"type": "string"}})
        self.assertEqual(spec.parameters["required"], [])

    def test_int_float_bool_are_mapped(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object, count: int, ratio: float, flag: bool) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(
            spec.parameters["properties"],
            {
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "flag": {"type": "boolean"},
            },
        )
        self.assertEqual(spec.parameters["required"], ["count", "ratio", "flag"])

    def test_unsupported_parameter_type_raises_at_decoration_time(self) -> None:
        with self.assertRaises(TypeError):

            @llm_tool(name="a_tool", description="Does a thing.")
            def command(self: object, ctx: object, members: list[str]) -> None: ...


class TestAnnotatedParameterDescriptions(unittest.TestCase):
    """The natural, single-metadata-item `Annotated[X, "description"]` --
    made safe on a real Discord command parameter by mutating
    `func.__annotations__` in place (see TestAnnotationsArePatchedInPlace
    below), not merely by reading it for the schema."""

    def test_annotated_optional_str_carries_its_description(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(
            self: object,
            ctx: object,
            timezone: Annotated[str | None, "An IANA time zone name."] = None,
        ) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(
            spec.parameters["properties"],
            {"timezone": {"type": "string", "description": "An IANA time zone name."}},
        )
        self.assertEqual(spec.parameters["required"], [])

    def test_annotated_required_int_carries_its_description_and_stays_required(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(
            self: object, ctx: object, count: Annotated[int, "How many to fetch."]
        ) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(
            spec.parameters["properties"],
            {"count": {"type": "integer", "description": "How many to fetch."}},
        )
        self.assertEqual(spec.parameters["required"], ["count"])

    def test_a_parameter_without_annotated_has_no_description(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object, name: str) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(spec.parameters["properties"], {"name": {"type": "string"}})

    def test_annotated_unsupported_base_type_still_raises(self) -> None:
        with self.assertRaises(TypeError):

            @llm_tool(name="a_tool", description="Does a thing.")
            def command(
                self: object, ctx: object, members: Annotated[list[str], "A list."]
            ) -> None: ...


class TestAnnotationsArePatchedInPlace(unittest.TestCase):
    """`@llm_tool` mutates `func.__annotations__` itself -- not merely a
    transient `func.__signature__` override -- specifically because a
    `__signature__` override does not survive discord.py's own repeated
    re-derivation of a command's signature (verified directly: real
    discord.py's `HybridAppCommand.__init__` borrows a hybrid command's
    `__signature__` to build its slash-command equivalent, then explicitly
    `del`s it in a `finally` block; every subsequent Cog instantiation --
    `Cog.__new__` copies each command fresh per instance -- then re-derives
    the signature from `__annotations__` alone, with no override left to
    find. A previous version of this decorator relied on `__signature__`
    alone and passed every test in this file, then broke a real bot's cog
    load in CI, which no test here could reach: the discord/redbot stub
    this repo tests against doesn't implement `Cog.__new__`'s per-instance
    command copying at all. This test asserts the one thing that survives
    that copying regardless: the callback's own `__annotations__` dict is
    permanently clean, not just the schema this decorator reports)."""

    def test_annotated_parameter_is_replaced_with_its_bare_type(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(
            self: object,
            ctx: object,
            timezone: Annotated[str | None, "An IANA time zone name."] = None,
        ) -> None: ...

        self.assertEqual(command.__annotations__["timezone"], str | None)

    def test_a_parameter_without_annotated_is_left_untouched(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object, name: str) -> None: ...

        # Still the PEP-563 string form (`from __future__ import
        # annotations` is in effect for this test module too) -- @llm_tool
        # has no reason to touch a parameter it never needed to change.
        self.assertEqual(command.__annotations__["name"], "str")

    def test_self_and_ctx_are_left_untouched(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(
            self: object,
            ctx: object,
            timezone: Annotated[str | None, "An IANA time zone name."] = None,
        ) -> None: ...

        self.assertEqual(command.__annotations__["self"], "object")
        self.assertEqual(command.__annotations__["ctx"], "object")


if __name__ == "__main__":
    unittest.main()
