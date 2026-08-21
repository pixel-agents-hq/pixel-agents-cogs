"""Read the vendor's own websocket message schemas out of its AsyncAPI spec.

`pixel-agents/core/asyncapi.yaml` is upstream's generated source of truth for
the webview's websocket protocol; each message type is a `components.schemas`
entry with `properties.type.const` naming the discriminator value the wire
message actually carries. Indexing by that discriminator (rather than by
schema name, which doesn't always match the `type` string) means a schema
lookup by a captured message's own `message["type"]` field just works, and
new message types upstream adds show up automatically -- nothing here needs
to be updated by hand when they do.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import RefResolver


class SchemaLoadError(RuntimeError):
    """`core/asyncapi.yaml` is missing or not shaped as expected."""


@dataclass(frozen=True)
class MessageSchemas:
    """A `type`-discriminator index plus a resolver for the `$ref`s message
    schemas make to shared definitions elsewhere in the same document (e.g.
    `ExistingAgents.agentMeta` -> `#/components/schemas/AgentSeatMeta`)."""

    by_type: dict[str, dict[str, Any]]
    resolver: RefResolver


def load_message_schemas(vendor_root: Path) -> MessageSchemas:
    """Map each discriminated message `type` string to its JSON schema.

    `vendor_root` is the checked-out pixel-agents repo root (the directory
    containing `core/asyncapi.yaml`), not the `core/` directory itself.
    """

    spec_path = vendor_root / "core" / "asyncapi.yaml"
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaLoadError(f"could not read {spec_path}: {exc}") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemaLoadError(f"{spec_path} is not valid YAML: {exc}") from exc

    schemas = (document or {}).get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict) or not schemas:
        raise SchemaLoadError(f"{spec_path} has no components.schemas")

    indexed: dict[str, dict[str, Any]] = {}
    for schema in schemas.values():
        if not isinstance(schema, dict):
            continue
        type_field = schema.get("properties", {}).get("type")
        if not isinstance(type_field, dict):
            continue
        const = type_field.get("const")
        if isinstance(const, str):
            indexed[const] = schema

    resolver = RefResolver.from_schema(document)
    return MessageSchemas(by_type=indexed, resolver=resolver)
