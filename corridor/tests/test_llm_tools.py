"""llm_tool/llm_tool_spec are testable under this repo's discord.py stub
(corridor.testing.install_stubs, installed package-wide by conftest.py) --
`corridor/adapters/llm_tools.py` genuinely needs discord.py's own
Parameter/Signature machinery (see that module's docstring for why), which
is why this suite -- unlike most of corridor's application/domain-layer
tests -- can't run with zero discord.py stubbing at all."""

from __future__ import annotations

import inspect
import unittest
from typing import Annotated

from ..adapters import llm_tool, llm_tool_spec


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
    verified separately (see docs/corridor-tool-registry-design.md) to be
    unsafe to leave in place on a real Discord command parameter, which is
    why `llm_tool` also patches `__signature__` -- covered by
    TestSignaturePatchedForDiscordPy below."""

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


class TestSignaturePatchedForDiscordPy(unittest.TestCase):
    """`@llm_tool` must strip `Annotated` back down to the bare type on the
    callback's own exposed signature, so that discord.py's own command
    construction -- which reads this exact signature next, when the outer
    `@x.command(...)` decorator wraps this same function -- never sees
    `Annotated` at all. See llm_tools.py's module docstring for why a
    plain `Annotated[X, "text"]` left in place is actually unsafe (not
    just untidy) on a real Discord command parameter."""

    def test_patched_signature_has_no_annotated_left(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(
            self: object,
            ctx: object,
            timezone: Annotated[str | None, "An IANA time zone name."] = None,
        ) -> None: ...

        patched = inspect.signature(command).parameters["timezone"]
        self.assertEqual(patched.annotation, str | None)
        self.assertEqual(patched.default, None)

    def test_patched_signature_leaves_self_and_ctx_untouched(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object) -> None: ...

        params = list(inspect.signature(command).parameters)
        self.assertEqual(params, ["self", "ctx"])

    def test_patched_parameters_still_resolve_required_and_converter(self) -> None:
        # These two properties don't exist on plain inspect.Parameter --
        # they're discord.ext.commands.Parameter's own additions, and are
        # exactly what a real Command's parameter resolution depends on at
        # both decoration and invocation time.
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(
            self: object,
            ctx: object,
            timezone: Annotated[str | None, "An IANA time zone name."] = None,
        ) -> None: ...

        patched = inspect.signature(command).parameters["timezone"]
        self.assertFalse(patched.required)  # type: ignore[attr-defined]
        self.assertEqual(patched.converter, str | None)  # type: ignore[attr-defined]

    def test_a_parameter_with_no_annotated_is_unaffected_by_patching(self) -> None:
        @llm_tool(name="a_tool", description="Does a thing.")
        def command(self: object, ctx: object, name: str) -> None: ...

        patched = inspect.signature(command).parameters["name"]
        self.assertEqual(patched.annotation, str)
        self.assertTrue(patched.required)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
