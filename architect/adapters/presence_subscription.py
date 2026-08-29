"""Feeds architect's own genuine-agent roster and message activity
(self._office_service's _genuine_agents, see
pixelagents/application/office.py) from corridor's AgentPresenceChanged
and AgentReplied events -- narrowed from floorplan's own
EventSubscriptionsMixin (floorplan/adapters/event_subscriptions.py),
which this mirrors in spirit but not in scope: architect has no Discord
member sync of its own (no guild scope at all, see
docs/architect-design.md section 6), so a Discord-account-shaped AgentRef
is silently ignored here -- only genuine-agent identities (AgentRef.agent_key
set) are relevant. Not imported from floorplan directly for the same
"duplicated, not shared" reason floorplan's own transport classes are
duplicated rather than imported (docs/architect-design.md section 5).

Also reconciles one entry corridor's AgentDirectoryService could never
supply: the bot's own Discord account. That account was never an A2A
agent -- it has no AgentCard/AgentExecutor and does not belong in
AgentDirectoryService -- so it is reconciled directly against
self._office_service, once, at cog_load, rather than round-tripped
through corridor's event bus at all. See docs/office-agent-identity-design.md.
"""

from __future__ import annotations

import asyncio
import logging

from corridor.domain import AgentPresenceChanged, AgentRef, AgentReplied
from pixelagents.domain import GenuineAgentKey

from .cog_base import CogBase

log = logging.getLogger("red.architect")

# Fixed, not configurable -- architect's dashboard has no settings panel
# equivalent to floorplan's message_tool_clear_delay yet. Same default
# floorplan/domain/models.py ships (2.0s).
_MESSAGE_ACTIVITY_CLEAR_DELAY = 2.0


def _own_bot_account_key(bot_user_id: int) -> GenuineAgentKey:
    # "discord-bot-" prefix keeps this namespace visibly distinct from a
    # real A2A agent_key slug ("architect", ...) even though a numeric
    # Discord snowflake could never collide with a short human-chosen
    # slug in practice.
    return GenuineAgentKey(agent_key=f"discord-bot-{bot_user_id}")


def _genuine_identity(agent: AgentRef) -> GenuineAgentKey | None:
    if agent.agent_key is None:
        return None
    return GenuineAgentKey(agent_key=agent.agent_key)


class PresenceSubscriptionMixin(CogBase):
    """Requires `self._corridor`, `self._office_service`, `self.bot`
    (all provided by CogBase)."""

    async def _start_presence_tracking(self) -> None:
        self._corridor.subscribe_event(
            AgentPresenceChanged, self._on_agent_presence_changed, owner="Architect"
        )
        self._corridor.subscribe_event(AgentReplied, self._on_agent_replied, owner="Architect")
        await self._reconcile_own_bot_account()

    async def _reconcile_own_bot_account(self) -> None:
        """Best-effort, must never fail cog_load: self.bot.user should
        always be set by the time a cog loads, but this stays defensive
        the same way _register_with_corridor/_publish_activity already
        are elsewhere in this package."""

        user = self.bot.user
        if user is None:
            return
        try:
            await self._office_service.reconcile_genuine_agent(
                _own_bot_account_key(user.id), user.name, "online"
            )
        except Exception:
            log.exception("architect: could not reconcile its own bot account onto the dashboard")

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unsubscribe_owner("Architect")
        await super().cog_unload()
        # Deliberately does not reconcile the own-bot-account entry (or
        # architect's own genuine-agent entry, published offline by
        # corridor's own unregister_agent_owner during super().cog_unload()
        # above, after this unsubscribe already dropped the handler that
        # would have caught it) to "offline" here -- the whole
        # self._office_service/self._client_hub instance is discarded with
        # this Cog object regardless (CogBase.__init__ builds a fresh one
        # on the next cog_load), so there is no persisted roster and no
        # still-connected client left to show a stale entry to. See
        # cog_base.py's `_start_presence_tracking` docstring for the
        # matching load-side ordering reasoning.

    async def _on_agent_presence_changed(self, event: AgentPresenceChanged) -> None:
        identity = _genuine_identity(event.agent)
        if identity is None:
            return
        await self._office_service.reconcile_genuine_agent(
            identity, event.display_name, event.status
        )

    async def _on_agent_replied(self, event: AgentReplied) -> None:
        """Mirrors floorplan's own `_on_agent_replied`
        (adapters/event_subscriptions.py) genuine-agent branch: both pico
        (consult_agent_tool.py/reply_tool.py) and architect itself
        (cog_base.py's `_publish_activity`) already publish this
        unconditionally onto corridor's shared bus -- only the
        `is_tracked` gate (an agent must already be on the roster via
        AgentPresenceChanged before its messages render) is new here,
        same gate floorplan applies."""

        identity = _genuine_identity(event.agent)
        if identity is None or not self._office_service.is_tracked(identity):
            return
        await self._office_service.send_genuine_agent_activity(identity, event.summary)
        self._create_background_task(
            self._clear_genuine_agent_activity_after_delay(identity),
            name=f"architect-agent-replied-clear-{identity.agent_key}",
        )

    async def _clear_genuine_agent_activity_after_delay(self, identity: GenuineAgentKey) -> None:
        await asyncio.sleep(_MESSAGE_ACTIVITY_CLEAR_DELAY)
        await self._office_service.clear_genuine_agent_activity(identity)


__all__ = ["PresenceSubscriptionMixin"]
