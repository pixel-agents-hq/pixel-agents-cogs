"""Generated-contract source for corridor's office-state event family."""

from __future__ import annotations

from typing import Any


def build_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "implemented",
        "source_doc": "docs/cctv-design.md",
        "state_kinds": ["discord", "editor"],
        "events": {
            "OfficeState": {
                "kind": "value-object",
                "fields": {
                    "kind": {"type": "OfficeStateKind"},
                    "layout": {"type": "dict[str, Any]"},
                    "seats": {"type": "dict[str, dict[str, Any]]"},
                    "revision": {"type": "int"},
                },
            },
            "OfficeStateChanged": {
                "kind": "event",
                "fields": {"state": {"type": "OfficeState"}},
                "subscriber_timeout_seconds": 5.0,
            },
        },
    }


__all__ = ["build_contract"]
