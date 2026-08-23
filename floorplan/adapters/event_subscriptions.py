"""Subscriber handlers for corridor's Discord-vocabulary Pub/Sub bus.

The only place floorplan translates a bus-originated event back into
pixelagents.domain snapshots and drives the *existing*
OfficeService/PresenceService entry points -- reconcile()/send_message_activity()
are called completely unchanged; only who calls them, and with what
reconstructed snapshot, is new here. See discord_gateway.py for the
publishing half of this split.
"""

from __future__ import annotations

import asyncio
import itertools

from corridor.domain import (
    AgentHighlighted,
    AgentPresenceChanged,
    AgentReplied,
    AgentStatusChanged,
    AgentToolStarted,
    AgentUnhighlighted,
)
from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    MessageSnapshot,
    PresenceStatus,
)

from .cog_base import PixelAgentsBase

# AgentReplied doesn't carry a real Discord message ID (it also covers
# non-message-based replies, e.g. a future corridor.send_reply call) --
# reconstructing MessageSnapshot needs *some* message_id, but nothing
# downstream depends on its real value: send_message_activity only uses it
# to compose a per-tool string ID, and clear_message_activity ignores it
# entirely (agentToolsClear clears every foreground tool for the agent, not
# by tool_id). A per-process counter is enough.
_synthetic_message_ids = itertools.count(1)


def _agent_snapshot(event: AgentPresenceChanged) -> AgentSnapshot:
    status = None if event.status == "offline" else PresenceStatus(event.status)
    return AgentSnapshot(
        key=AgentKey(guild_id=event.agent.guild_id, user_id=event.agent.discord_user_id),
        display_name=event.display_name,
        status=status,
        is_bot=event.agent.is_bot,
        activities=tuple(
            ActivitySnapshot(
                kind=ActivityKind(activity.kind),
                name=activity.name,
                title=activity.title,
                artist=activity.artist,
                details=activity.details,
                state=activity.state,
            )
            for activity in event.activities
        ),
    )


class EventSubscriptionsMixin(PixelAgentsBase):
    """Owns its own subscription lifecycle -- wraps cog_base.py's
    cog_load/cog_unload rather than needing a hook stub declared there, so
    this concern stays entirely contained in this module. Registration
    happens right after cog_load's own corridor.register_dependent() call
    (both cascade-unload/no-op the same way if corridor itself unloads
    first); by the time cog_load actually returns, Red hasn't started
    dispatching Discord gateway events to this Cog yet either way, so the
    exact ordering relative to the rest of cog_load's startup work (pixelagents
    loading, the websocket server, initial sync) doesn't matter."""

    async def cog_load(self) -> None:
        await super().cog_load()
        self._corridor.subscribe_event(
            AgentPresenceChanged, self._on_agent_presence_changed, owner="Floorplan"
        )
        self._corridor.subscribe_event(AgentReplied, self._on_agent_replied, owner="Floorplan")
        self._corridor.subscribe_event(
            AgentHighlighted, self._on_agent_highlighted, owner="Floorplan"
        )
        self._corridor.subscribe_event(
            AgentUnhighlighted, self._on_agent_unhighlighted, owner="Floorplan"
        )
        self._corridor.subscribe_event(
            AgentToolStarted, self._on_agent_tool_started, owner="Floorplan"
        )
        self._corridor.subscribe_event(
            AgentStatusChanged, self._on_agent_status_changed, owner="Floorplan"
        )

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unsubscribe_owner("Floorplan")
        await super().cog_unload()

    async def _on_agent_presence_changed(self, event: AgentPresenceChanged) -> None:
        guild_settings = await self._settings_repository.guild_settings(event.agent.guild_id)
        if not guild_settings.enabled:
            return
        await self._office_service.reconcile(
            _agent_snapshot(event),
            include_bots=guild_settings.include_bots,
            rich_presence_enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    async def _on_agent_replied(self, event: AgentReplied) -> None:
        key = AgentKey(guild_id=event.agent.guild_id, user_id=event.agent.discord_user_id)
        if not self._office_service.is_tracked(key):
            return
        snapshot = MessageSnapshot(
            key=key, message_id=next(_synthetic_message_ids), content=event.summary
        )
        await self._office_service.send_message_activity(snapshot)
        delay = await self._settings_repository.message_tool_clear_delay()
        self._task_supervisor.create(
            self._clear_tool_after_delay(
                self._office_service.agent_id(key.user_id), delay, key.guild_id, key.user_id
            ),
            name=f"floorplan-agent-replied-clear-{key.guild_id}-{key.user_id}",
        )

    async def _on_agent_highlighted(self, event: AgentHighlighted) -> None:
        key = AgentKey(guild_id=event.agent.guild_id, user_id=event.agent.discord_user_id)
        if not self._office_service.is_tracked(key):
            return
        await self._office_service.highlight_agent(key)

    async def _on_agent_unhighlighted(self, event: AgentUnhighlighted) -> None:
        key = AgentKey(guild_id=event.agent.guild_id, user_id=event.agent.discord_user_id)
        if not self._office_service.is_tracked(key):
            return
        await self._office_service.unhighlight_agent(key)

    async def _on_agent_tool_started(self, event: AgentToolStarted) -> None:
        key = AgentKey(guild_id=event.agent.guild_id, user_id=event.agent.discord_user_id)
        if not self._office_service.is_tracked(key):
            return
        await self._office_service.start_tool_activity(
            key, event.tool_id, event.status, event.tool_name
        )

    async def _on_agent_status_changed(self, event: AgentStatusChanged) -> None:
        key = AgentKey(guild_id=event.agent.guild_id, user_id=event.agent.discord_user_id)
        if not self._office_service.is_tracked(key):
            return
        await self._office_service.set_status(key, event.status, event.awaiting_input)

    async def _clear_tool_after_delay(
        self, agent_id: int, delay: float, guild_id: int = 0, user_id: int = 0
    ) -> None:
        # Moved here from discord_gateway.py -- only ever invoked from
        # _on_agent_replied now, never from a raw gateway listener.
        await asyncio.sleep(delay)
        if self._closing:
            return
        if guild_id and user_id:
            await self._office_service.clear_message_activity(AgentKey(guild_id, user_id))
        else:
            await self._send({"type": "agentToolsClear", "id": agent_id})
