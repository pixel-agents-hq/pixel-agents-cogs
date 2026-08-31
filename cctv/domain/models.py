"""Pure business models. Zero framework imports.

cctv's own domain logic is deliberately thin: office-state schema and
validation live in pixelagents' `OfficeStateFacade`
(`pixelagents/application/office_state.py`), and the two aggregate kinds
themselves are corridor's `OfficeStateKind`
(`corridor/domain/office_state.py`) -- both already framework-neutral, so
cctv has no reason to duplicate either here. This package exists to keep
the same domain/application/infrastructure/adapters layering every cog
in this repo uses, not because cctv has much pure business logic of its
own, the same "thin/empty and that's fine" precedent
docs/painter-design.md's own domain layer note already sets.
"""

from __future__ import annotations
