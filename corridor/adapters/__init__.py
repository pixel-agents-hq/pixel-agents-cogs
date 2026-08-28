from typing import TYPE_CHECKING

from .commands import CommandsMixin

if TYPE_CHECKING:
    from .cog_base import CogBase

__all__ = ["CogBase", "CommandsMixin"]


def __getattr__(name: str) -> object:
    # CogBase pulls in AgentDirectoryService (corridor/application/
    # agent_directory_service.py), which needs a2a-sdk -- deferred here so
    # `from corridor.adapters.api import build_reply_payload` (pixelagents'
    # production reply path, which never installs a2a-sdk) doesn't import
    # it just by importing this package. See corridor/domain/__init__.py's
    # matching __getattr__ for the same reasoning one layer down.
    if name == "CogBase":
        from .cog_base import CogBase

        return CogBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
