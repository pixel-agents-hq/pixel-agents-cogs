"""Feeds the editor pipeline's roster (genuine A2A agents plus cctv's own
bot Discord account -- docs/cctv-design.md §2.7's table) from corridor's
AgentPresenceChanged/AgentReplied events. Ported from architect's former
`adapters/presence_subscription.py` (now retired there, see
docs/cctv-design.md) -- narrower than the Discord pipeline's own
subscriber (`event_subscriptions_discord.py`): a Discord-account-shaped
`AgentRef` for any *other* real Discord member is ignored here, only
genuine-agent identities (and cctv's own bot account) are reconciled.
"""

from __future__ import annotations

import asyncio
import logging

from corridor.domain import AgentPresenceChanged, AgentRef, AgentReplied
from pixelagents.domain import GenuineAgentKey

from .cog_base import CogBase

log = logging.getLogger("red.cctv")

_MESSAGE_ACTIVITY_CLEAR_DELAY = 2.0


def _own_bot_account_key(bot_user_id: int) -> GenuineAgentKey:
    return GenuineAgentKey(agent_key=f"discord-bot-{bot_user_id}")


def _genuine_identity(agent: AgentRef) -> GenuineAgentKey | None:
    if agent.agent_key is None:
        return None
    return GenuineAgentKey(agent_key=agent.agent_key)


def _reply_identity(agent: AgentRef, own_bot_user_id: int | None) -> GenuineAgentKey | None:
    """Wider than `_genuine_identity`: pico's own `AgentReplied` publishes
    attribute themselves to a Discord-account-shaped `AgentRef` (pico has
    no `agent_key` of its own). Narrowly recognize just the one Discord-
    account identity the editor pipeline actually tracks: cctv's own
    bot's account, seeded once at cog_load by `_reconcile_own_bot_account`
    under the same `discord-bot-{id}` key built here."""

    if agent.agent_key is not None:
        return GenuineAgentKey(agent_key=agent.agent_key)
    if (
        agent.is_bot
        and agent.discord_user_id is not None
        and own_bot_user_id is not None
        and agent.discord_user_id == own_bot_user_id
    ):
        return _own_bot_account_key(agent.discord_user_id)
    return None


class EventSubscriptionsEditorMixin(CogBase):
    """Requires `self._corridor`, `self._editor_office_service`, `self.bot`,
    `self._create_background_task` (all provided by `CogBase`)."""

    async def cog_load(self) -> None:
        await super().cog_load()
        # watch_agents (not two separate subscribe_event calls) atomically
        # subscribes below AND returns the current A2A roster in one call
        # -- closes the confirmed cold-start gap where a fresh subscriber
        # could miss an agent that registered before it subscribed
        # (docs/cctv-design.md §1.4/§2.2; this is precisely the gap
        # architect's own former presence subscriber never closed).
        roster = self._corridor.watch_agents(
            {
                AgentPresenceChanged: self._on_editor_agent_presence_changed,
                AgentReplied: self._on_editor_agent_replied,
            },
            owner="Cctv",
        )
        for agent in roster:
            await self._editor_office_service.reconcile_genuine_agent(
                GenuineAgentKey(agent_key=agent.agent_key),
                agent.card.name or agent.agent_key,
                "online",
            )
        await self._reconcile_own_bot_account()

    async def _reconcile_own_bot_account(self) -> None:
        """Best-effort, must never fail cog_load: `self.bot.user` should
        always be set by the time a cog loads, but this stays defensive
        the same way architect's original version already was."""

        user = self.bot.user
        if user is None:
            return
        try:
            await self._editor_office_service.reconcile_genuine_agent(
                _own_bot_account_key(user.id), user.name, "online"
            )
        except Exception:
            log.exception("cctv: could not reconcile its own bot account onto the editor page")

    async def _on_editor_agent_presence_changed(self, event: AgentPresenceChanged) -> None:
        identity = _genuine_identity(event.agent)
        if identity is None:
            return
        await self._editor_office_service.reconcile_genuine_agent(
            identity, event.display_name, event.status
        )

    async def _on_editor_agent_replied(self, event: AgentReplied) -> None:
        own_bot_user_id = self.bot.user.id if self.bot.user is not None else None
        identity = _reply_identity(event.agent, own_bot_user_id)
        if identity is None or not self._editor_office_service.is_tracked(identity):
            return
        await self._editor_office_service.send_genuine_agent_activity(identity, event.summary)
        self._create_background_task(
            self._clear_editor_activity_after_delay(identity),
            name=f"cctv-editor-agent-replied-clear-{identity.agent_key}",
        )

    async def _clear_editor_activity_after_delay(self, identity: GenuineAgentKey) -> None:
        await asyncio.sleep(_MESSAGE_ACTIVITY_CLEAR_DELAY)
        await self._editor_office_service.clear_genuine_agent_activity(identity)


__all__ = ["EventSubscriptionsEditorMixin"]
