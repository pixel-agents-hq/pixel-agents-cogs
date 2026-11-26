"""Index the distributions importable from a list of sys.path entries."""

from __future__ import annotations

import importlib.metadata
import json
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
            InstalledDistribution(
                version=dist.version,
                requires=tuple(dist.requires or ()),
                direct_url=direct_url(dist.read_text("direct_url.json")),
            ),
        )
    return index.get


def direct_url(text: str | None) -> str | None:
    """pip's `direct_url.json` (PEP 610) as a PEP 508 URL, or None."""

    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("url"), str):
        return None
    url: str = data["url"]
    vcs_info = data.get("vcs_info")
    if isinstance(vcs_info, dict) and isinstance(vcs_info.get("vcs"), str):
        url = f"{vcs_info['vcs']}+{url}"
        revision = vcs_info.get("requested_revision")
        if isinstance(revision, str) and revision:
            url = f"{url}@{revision}"
    subdirectory = data.get("subdirectory")
    if isinstance(subdirectory, str) and subdirectory:
        url = f"{url}#subdirectory={subdirectory}"
    return url
