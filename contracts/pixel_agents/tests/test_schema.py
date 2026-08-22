"""Unit tests for contracts.pixel_agents.schema -- fully offline, no vendor clone."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contracts.pixel_agents.schema import SchemaLoadError, load_message_schemas

_FAKE_SPEC = """
components:
  schemas:
    AgentClosed:
      type: object
      additionalProperties: false
      required: [type, id]
      properties:
        type:
          const: agentClosed
        id:
          type: integer
    AgentSeatMeta:
      type: object
      properties:
        palette:
          type: integer
    ExistingAgents:
      type: object
      additionalProperties: false
      required: [type, agentMeta]
      properties:
        type:
          const: existingAgents
        agentMeta:
          type: object
          additionalProperties:
            $ref: '#/components/schemas/AgentSeatMeta'
    LaunchAgent:
      type: object
      description: Client-to-server message, has no discriminator const in this fixture.
      properties:
        type:
          type: string
"""


def _write_spec(root: Path, text: str = _FAKE_SPEC) -> None:
    core = root / "core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "asyncapi.yaml").write_text(text, encoding="utf-8")


class TestLoadMessageSchemas(unittest.TestCase):
    def test_indexes_schemas_by_their_type_const(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_spec(root)
            schemas = load_message_schemas(root)

        self.assertIn("agentClosed", schemas.by_type)
        self.assertEqual(schemas.by_type["agentClosed"]["required"], ["type", "id"])

    def test_excludes_schemas_without_a_const_discriminator(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_spec(root)
            schemas = load_message_schemas(root)

        self.assertNotIn("AgentSeatMeta", schemas.by_type)
        self.assertNotIn("LaunchAgent", schemas.by_type)

    def test_resolver_follows_refs_to_sibling_schemas(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_spec(root)
            schemas = load_message_schemas(root)

        from jsonschema import Draft7Validator

        schema = schemas.by_type["existingAgents"]
        validator = Draft7Validator(schema, resolver=schemas.resolver)
        errors = list(
            validator.iter_errors(
                {"type": "existingAgents", "agentMeta": {"1": {"palette": "not-an-int"}}}
            )
        )
        self.assertTrue(errors, "expected the $ref'd AgentSeatMeta schema to be enforced")

    def test_missing_file_raises_a_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SchemaLoadError, "could not read"):
                load_message_schemas(Path(tmp))

    def test_malformed_yaml_raises_a_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_spec(root, text="not: valid: yaml: [")
            with self.assertRaisesRegex(SchemaLoadError, "not valid YAML"):
                load_message_schemas(root)

    def test_missing_schemas_section_raises_a_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_spec(root, text="components: {}\n")
            with self.assertRaisesRegex(SchemaLoadError, "components.schemas"):
                load_message_schemas(root)


if __name__ == "__main__":
    unittest.main()
