"""Red entrypoint with a dependency-aware import.

The straightforward `from .switchboard import ...` would force
Red/discord.py to be importable the moment this package is imported at all --
including by pytest's conftest.py discovery, which must import this
`__init__.py` before it can reach `conftest.py` in this same package. Defer
the real import so the package stays importable (and tests' stub modules get
a chance to install) even without Red/discord.py present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

try:
    from redbot.core.utils import get_end_user_data_statement_or_raise
except ImportError:
    # Without Red (tests, contract tooling): the same info.json key Red reads.
    import json
    from pathlib import Path

    __red_end_user_data_statement__ = json.loads(
        Path(__file__).with_name("info.json").read_text(encoding="utf-8")
    )["end_user_data_statement"]
else:
    __red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: Red) -> None:
    # No corridor bootstrap: switchboard is the cog that makes corridor
    # loadable, so it must load without it (see adapters/cog_base.py).
    from .switchboard import Switchboard

    await bot.add_cog(Switchboard(bot))
