"""Index the distributions importable from a list of sys.path entries."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Sequence

from packaging.utils import canonicalize_name

from ..domain import DistributionLookup, InstalledDistribution


def build_lookup(paths: Sequence[str]) -> DistributionLookup:
    """A lookup over `paths`, first match winning -- the same precedence
    imports follow. Red appends Downloader's lib dir *after* site-packages,
    so a copy pip put in the lib dir is invisible while site-packages has
    the same distribution, and this lookup reports the site-packages one."""

    index: dict[str, InstalledDistribution] = {}
    for dist in importlib.metadata.distributions(path=list(paths)):
        name = dist.metadata["Name"]
        if not name:
            continue
        index.setdefault(
            canonicalize_name(name),
            InstalledDistribution(version=dist.version, requires=tuple(dist.requires or ())),
        )
    return index.get
