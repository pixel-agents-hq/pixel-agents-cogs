"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.office_commands import OfficeCommandsMixin


class Architect(
    CommandsMixin,
    OfficeCommandsMixin,
    CogBase,
    commands.Cog,
):
    """A second, independent LLM agent reachable only over A2A -- never
    Discord-user-facing. Shares corridor's LLM connection with pico."""
