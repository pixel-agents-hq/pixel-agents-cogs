"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.dashboard import DashboardMixin
from .adapters.office_commands import OfficeCommandsMixin
from .adapters.office_gateway import OfficeGatewayMixin


class Architect(
    CommandsMixin, OfficeCommandsMixin, DashboardMixin, OfficeGatewayMixin, CogBase, commands.Cog
):
    """A second, independent LLM agent reachable only over A2A -- never
    Discord-user-facing. Shares corridor's LLM connection with pico."""
