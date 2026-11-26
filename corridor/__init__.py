"""Red entrypoint with a dependency-aware import.

The straightforward `from .corridor import ...` would force Red/discord.py to
be importable the moment this package is imported at all -- including by
pytest's conftest.py discovery, which must import this `__init__.py` before
it can reach `conftest.py` in this same package. Defer the real import so
the package stays importable (and tests' stub modules get a chance to
install) even without Red/discord.py present.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .requirement_hint import pin_break_message

if TYPE_CHECKING:
    from redbot.core.bot import Red

_INFO = json.loads(Path(__file__).with_name("info.json").read_text(encoding="utf-8"))
# Named in the load error when a pinned requirement no longer holds.
_REQUIREMENTS: tuple[str, ...] = tuple(_INFO.get("requirements") or ())

try:
    from redbot.core.utils import get_end_user_data_statement_or_raise
except ImportError:
    # Without Red (tests, contract tooling): the same info.json key Red reads.
    __red_end_user_data_statement__ = _INFO["end_user_data_statement"]
else:
    __red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: Red) -> None:
    try:
        from .corridor import Corridor
    except (ImportError, SystemError) as exc:
        message = pin_break_message(exc, _REQUIREMENTS)
        if message is None:
            raise
        from redbot.core.errors import CogLoadError

        raise CogLoadError(message) from exc

    await bot.add_cog(Corridor(bot))
