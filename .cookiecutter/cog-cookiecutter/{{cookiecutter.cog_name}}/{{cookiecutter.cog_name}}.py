"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(CommandsMixin, CogBase, commands.Cog):
    """{{ cookiecutter.short }}"""
