"""Subscriber handlers feeding the Discord pipeline's roster from
corridor's Discord-vocabulary Pub/Sub bus. Ported from floorplan's former
`adapters/event_subscriptions.py` (now retired there, see
docs/cctv-design.md) -- already handled BOTH real Discord members (gated
on the enabled-guild settings below) and genuine A2A agents (ungated)
before this move, matching the Discord page's roster exactly
(docs/cctv-design.md §2.7's table: "enabled-guild Discord members plus
all registered A2A agents").
"""

from __future__ import annotations

import asyncio
import itertools

from corridor.domain import (
    AgentHighlighted,
    AgentPresenceChanged,
    AgentRef,
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
    GenuineAgentKey,
    MessageSnapshot,
    OfficeIdentity,
    PresenceStatus,
)

from .cog_base import CogBase

# AgentReplied doesn't carry a real Discord message ID -- reconstructing
# MessageSnapshot needs *some* message_id, but nothing downstream depends
# on its real value. A per-process counter is enough.
_synthetic_message_ids = itertools.count(1)


def _office_identity(agent: AgentRef) -> OfficeIdentity | None:
    if agent.guild_id is not None and agent.discord_user_id is not None:
        return AgentKey(guild_id=agent.guild_id, user_id=agent.discord_user_id)
    if agent.agent_key is not None:
        return GenuineAgentKey(agent_key=agent.agent_key)
    return None


def _agent_snapshot(event: AgentPresenceChanged, key: AgentKey) -> AgentSnapshot:
    status = None if event.status == "offline" else PresenceStatus(event.status)
    return AgentSnapshot(
        key=key,
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


class EventSubscriptionsDiscordMixin(CogBase):
    """Requires `self._corridor`, `self._discord_office_service`,
    `self._repository`, `self._create_background_task` (all provided by
    `CogBase`)."""

    async def cog_load(self) -> None:
        await super().cog_load()
        # watch_agents (not six separate subscribe_event calls) is what
        # closes the confirmed cold-start gap: it atomically subscribes
        # every handler below AND returns the current A2A roster in one
        # call, so an agent that registered before cctv's own cog_load
        # ran is never missed (docs/cctv-design.md §1.4/§2.2).
        roster = self._corridor.watch_agents(
            {
                AgentPresenceChanged: self._on_discord_agent_presence_changed,
                AgentReplied: self._on_discord_agent_replied,
                AgentHighlighted: self._on_discord_agent_highlighted,
                AgentUnhighlighted: self._on_discord_agent_unhighlighted,
                AgentToolStarted: self._on_discord_agent_tool_started,
                AgentStatusChanged: self._on_discord_agent_status_changed,
            },
            owner="Cctv",
        )
        for agent in roster:
            await self._discord_office_service.reconcile_genuine_agent(
                GenuineAgentKey(agent_key=agent.agent_key),
                agent.card.name or agent.agent_key,
                "online",
            )

    async def _on_discord_agent_presence_changed(self, event: AgentPresenceChanged) -> None:
        identity = _office_identity(event.agent)
        if identity is None:
            return
        if isinstance(identity, GenuineAgentKey):
            # No guild scope applies -- a genuine agent renders on the one
            # shared canvas unconditionally.
            await self._discord_office_service.reconcile_genuine_agent(
                identity, event.display_name, event.status
            )
            return
        guild_settings = await self._repository.guild_settings(identity.guild_id)
        if not guild_settings.enabled:
            return
        global_settings = await self._repository.global_settings()
        await self._discord_office_service.reconcile(
            _agent_snapshot(event, identity),
            include_bots=guild_settings.include_bots,
            rich_presence_enabled=global_settings.broadcast_rich_presence,
        )

    async def _on_discord_agent_replied(self, event: AgentReplied) -> None:
        identity = _office_identity(event.agent)
        if identity is None or not self._discord_office_service.is_tracked(identity):
            return
        global_settings = await self._repository.global_settings()
        if isinstance(identity, GenuineAgentKey):
            await self._discord_office_service.send_genuine_agent_activity(identity, event.summary)
            agent_id = self._discord_office_service.genuine_agent_id(identity.agent_key)
            self._create_background_task(
                self._clear_genuine_tool_after_delay(agent_id, global_settings.discord_clear_delay),
                name=f"cctv-discord-agent-replied-clear-{identity.agent_key}",
            )
            return
        guild_settings = await self._repository.guild_settings(identity.guild_id)
        if not guild_settings.enabled or not global_settings.broadcast_messages:
            return
        snapshot = MessageSnapshot(
            key=identity, message_id=next(_synthetic_message_ids), content=event.summary
        )
        await self._discord_office_service.send_message_activity(snapshot)
        self._create_background_task(
            self._clear_message_activity_after_delay(identity, global_settings.discord_clear_delay),
            name=f"cctv-discord-agent-replied-clear-{identity.guild_id}-{identity.user_id}",
        )

    async def _on_discord_agent_highlighted(self, event: AgentHighlighted) -> None:
        identity = _office_identity(event.agent)
        if identity is None or not self._discord_office_service.is_tracked(identity):
            return
        await self._discord_office_service.highlight_agent(identity)

    async def _on_discord_agent_unhighlighted(self, event: AgentUnhighlighted) -> None:
        identity = _office_identity(event.agent)
        if identity is None or not self._discord_office_service.is_tracked(identity):
            return
        await self._discord_office_service.unhighlight_agent(identity)

    async def _on_discord_agent_tool_started(self, event: AgentToolStarted) -> None:
        identity = _office_identity(event.agent)
        if identity is None or not self._discord_office_service.is_tracked(identity):
            return
        await self._discord_office_service.start_tool_activity(
            identity, event.tool_id, event.status, event.tool_name
        )

    async def _on_discord_agent_status_changed(self, event: AgentStatusChanged) -> None:
        identity = _office_identity(event.agent)
        if identity is None or not self._discord_office_service.is_tracked(identity):
            return
        await self._discord_office_service.set_status(identity, event.status, event.awaiting_input)

    async def _clear_genuine_tool_after_delay(self, agent_id: int, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._send_discord({"type": "agentToolsClear", "id": agent_id})

    async def _clear_message_activity_after_delay(self, identity: AgentKey, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._discord_office_service.clear_message_activity(identity)


__all__ = ["EventSubscriptionsDiscordMixin"]
