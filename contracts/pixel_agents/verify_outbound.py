"""Verify pixelagents' outbound websocket helpers against the vendor's own schemas.

`pixelagents/contracts/outbound.py`'s `TypedDict`s and builders, and the
`OfficeService`/`PresenceService` application classes that call them, claim to
match the vendored `pixel-agents/core/asyncapi.yaml` message contract -- but
nothing previously checked that claim against the real spec, so an upstream
shape change would go unnoticed. This drives those helpers through a realistic
sequence, captures every message they actually emit, and validates each one
against its `type`-matched schema straight out of the checkout `verify.py`
already clones (see `schema.py`). `merge_seat_patch`/`to_agent_id` don't emit
wire messages, so they get a light functional smoke check instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from jsonschema import Draft7Validator

from pixelagents.application.office import OfficeService, merge_seat_patch, to_agent_id
from pixelagents.contracts import outbound
from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    MessageSnapshot,
    PresenceStatus,
)

from .schema import load_message_schemas

_MutationResult = TypeVar("_MutationResult")


class _MemorySeats:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def seats(self) -> dict[str, dict[str, object]]:
        return {key: dict(value) for key, value in self.values.items()}

    async def mutate_seats(
        self, mutation: Callable[[dict[str, dict[str, object]]], _MutationResult]
    ) -> _MutationResult:
        return mutation(self.values)


def _agent_snapshot(
    *, guild_id: int = 1, user_id: int = 2, name: str = "octocat", online: bool = True
) -> AgentSnapshot:
    activities = (
        (ActivitySnapshot(kind=ActivityKind.LISTENING, title="A Song", artist="A Band"),)
        if online
        else ()
    )
    return AgentSnapshot(
        key=AgentKey(guild_id, user_id),
        display_name=name,
        status=PresenceStatus.ONLINE if online else None,
        is_bot=False,
        activities=activities,
    )


async def _capture_messages() -> list[dict[str, Any]]:
    """Drive OfficeService/PresenceService through a realistic sequence,
    capturing every message they emit, plus a direct call to every remaining
    outbound.py builder no scripted flow above naturally produces."""

    captured: list[dict[str, Any]] = []

    async def send(message: Any) -> None:
        captured.append(dict(message))

    repository = _MemorySeats()
    service = OfficeService(repository, send)

    snapshot = _agent_snapshot()
    await service.spawn(snapshot, rich_presence_enabled=True)
    message = MessageSnapshot(key=snapshot.key, message_id=123, content="hello office")
    await service.send_message_activity(message)
    await service.clear_message_activity(snapshot.key)
    renamed = _agent_snapshot(name="octocat-2")
    await service.reconcile(renamed, include_bots=False, rich_presence_enabled=True)
    await service.close(snapshot.key)

    for entry in service.bootstrap_messages(
        assets={
            "characters": [],
            "floors": [],
            "walls": [],
            "carpets": [],
            "catalog": [],
            "furniture": {},
        },
        seats=await repository.seats(),
        layout={"tiles": []},
    ):
        captured.append(dict(entry))

    # Builders no scripted OfficeService/PresenceService flow above reaches.
    captured.append(dict(outbound.agent_tool_permission(1)))
    captured.append(dict(outbound.agent_tool_permission_clear(1)))
    captured.append(dict(outbound.subagent_tool_start(1, "tool-1", "sub-1", "Reading foo.ts")))
    captured.append(dict(outbound.subagent_tool_done(1, "tool-1", "sub-1")))
    captured.append(dict(outbound.subagent_clear(1, "tool-1")))
    captured.append(dict(outbound.subagent_tool_permission(1, "tool-1")))
    captured.append(dict(outbound.agent_context_usage(1, 1000, 200000)))
    captured.append(dict(outbound.agent_created(1, "octocat", 0, 0, is_external=True)))
    captured.append(
        dict(
            outbound.agent_team_info(
                1,
                "octocat",
                team_name="Alpha",
                is_team_lead=True,
                lead_agent_id=1,
                team_uses_tmux=False,
            )
        )
    )
    captured.append(dict(outbound.agent_status(1, "waiting", awaiting_input=True)))

    return captured


def _check_outbound_messages(vendor_dir: Path) -> tuple[bool, str]:
    schemas = load_message_schemas(vendor_dir)
    messages = asyncio.run(_capture_messages())

    problems: list[str] = []
    checked = 0
    for message in messages:
        message_type = message.get("type")
        schema = schemas.by_type.get(message_type) if isinstance(message_type, str) else None
        if schema is None:
            problems.append(f"no vendor schema for message type {message_type!r}")
            continue
        validator = Draft7Validator(schema, resolver=schemas.resolver)
        errors = sorted(validator.iter_errors(message), key=lambda e: e.path)
        if errors:
            detail = "; ".join(e.message for e in errors)
            problems.append(f"{message_type!r} failed validation: {detail}")
            continue
        checked += 1

    if problems:
        return False, "; ".join(problems)
    return True, f"{checked} captured message(s) matched the vendor schema"


def _check_helper_smoke() -> tuple[bool, str]:
    seats: dict[str, dict[str, object]] = {"7": {"palette": 1, "hueShift": 30, "seatId": "a1"}}
    merge_seat_patch(seats, "7", 6, {"palette": 3, "hueShift": 999, "seatId": "b2"})
    if seats["7"]["palette"] != 3:
        return False, "merge_seat_patch did not apply an in-range palette"
    if seats["7"]["hueShift"] != 30:
        return False, "merge_seat_patch did not reject an out-of-range hueShift"
    if seats["7"]["seatId"] != "b2":
        return False, "merge_seat_patch did not apply a valid seatId"

    first = to_agent_id(123456789012345678)
    second = to_agent_id(123456789012345678)
    if first != second:
        return False, "to_agent_id is not deterministic"
    if first >= 0:
        return False, "to_agent_id did not return a negative id"

    return True, ""


def run(vendor_dir: Path) -> list[dict[str, str]]:
    """Run the new checks, in the `{"name", "status", "detail"}` shape
    `contracts.pixel_agents.verify.run` already appends to its check list."""

    checks: list[dict[str, str]] = []
    named_checks: tuple[tuple[str, Callable[[], tuple[bool, str]]], ...] = (
        ("outbound_messages", lambda: _check_outbound_messages(vendor_dir)),
        ("helper_smoke", _check_helper_smoke),
    )
    for name, check in named_checks:
        ok, detail = check()
        checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})
    return checks
