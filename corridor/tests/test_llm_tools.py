"""llm_tool/llm_tool_spec are fully testable without Red: they only
inspect the decorated callable's own signature, no discord.py/redbot
stubbing needed."""

from __future__ import annotations

import unittest

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

    def test_decorator_returns_the_original_callable_unchanged(self) -> None:
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

    def test_parameter_descriptions_are_attached_to_the_matching_property(self) -> None:
        @llm_tool(
            name="a_tool",
            description="Does a thing.",
            parameter_descriptions={"timezone": "An IANA time zone name."},
        )
        def command(self: object, ctx: object, timezone: str | None = None) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(
            spec.parameters["properties"],
            {"timezone": {"type": "string", "description": "An IANA time zone name."}},
        )

    def test_a_parameter_with_no_description_has_none_in_the_schema(self) -> None:
        @llm_tool(
            name="a_tool",
            description="Does a thing.",
            parameter_descriptions={"name": "Who to greet."},
        )
        def command(self: object, ctx: object, name: str, count: int) -> None: ...

        spec = llm_tool_spec(command)
        assert spec is not None
        self.assertEqual(
            spec.parameters["properties"],
            {
                "name": {"type": "string", "description": "Who to greet."},
                "count": {"type": "integer"},
            },
        )

    def test_a_description_for_an_unknown_parameter_raises_at_decoration_time(self) -> None:
        with self.assertRaises(TypeError):

            @llm_tool(
                name="a_tool",
                description="Does a thing.",
                parameter_descriptions={"nonexistent": "..."},
            )
            def command(self: object, ctx: object, name: str) -> None: ...


if __name__ == "__main__":
    unittest.main()
