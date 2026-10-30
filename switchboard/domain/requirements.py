"""Checking info.json `requirements` against what is importable right now.

Uses `packaging`, which Red-DiscordBot itself depends on -- switchboard adds
no requirement of its own. No discord/redbot import here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion


@dataclass(frozen=True, slots=True)
class InstalledDistribution:
    version: str
    # Raw Requires-Dist lines, used to check a requested extra's own deps.
    requires: tuple[str, ...] = ()


#: canonical distribution name -> the distribution Python would import.
DistributionLookup = Callable[[str], "InstalledDistribution | None"]


def unsatisfied(
    requirements: Iterable[str],
    lookup: DistributionLookup,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Every requirement string not satisfied by the installed distributions.

    A requirement whose marker does not apply to this interpreter is
    satisfied. A requested extra (`a2a-sdk[http-server]`) is satisfied only
    if the distribution's own extra-gated requirements are too. A bare VCS
    or archive URL (`git+https://.../AAA3A_utils.git`) is satisfied when a
    distribution named after its `#egg=` fragment or its last path segment
    is installed, at any version. Any other unparseable requirement is
    always reported, so pip gets to decide.
    """

    source = environment if environment is not None else default_environment()
    env = {str(key): str(value) for key, value in source.items()}
    return tuple(raw for raw in requirements if not _satisfied(raw, lookup, env, visited=set()))


def _satisfied(
    raw: str,
    lookup: DistributionLookup,
    env: dict[str, str],
    *,
    visited: set[tuple[str, str]],
    extra: str = "",
) -> bool:
    try:
        requirement = Requirement(raw)
    except InvalidRequirement:
        guessed = _name_from_url(raw)
        return guessed is not None and lookup(canonicalize_name(guessed)) is not None
    if requirement.marker is not None and not requirement.marker.evaluate({**env, "extra": extra}):
        return True

    name = canonicalize_name(requirement.name)
    installed = lookup(name)
    if installed is None:
        return False
    if requirement.specifier:
        try:
            if not requirement.specifier.contains(installed.version, prereleases=True):
                return False
        except InvalidVersion:
            return False

    for wanted in sorted(requirement.extras):
        key = (name, canonicalize_name(wanted))
        if key in visited:
            continue
        visited.add(key)
        for dep in _extra_only_requirements(installed.requires, wanted, env):
            if not _satisfied(dep, lookup, env, visited=visited):
                return False
    return True


def _extra_only_requirements(requires: Iterable[str], extra: str, env: dict[str, str]) -> list[str]:
    """Requires-Dist lines that apply only when `extra` is requested.

    Base dependencies (no marker, or a marker true without the extra) are
    pip's job to have installed with the distribution itself.
    """

    selected: list[str] = []
    for raw in requires:
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            continue
        marker = requirement.marker
        if marker is None:
            continue
        if marker.evaluate({**env, "extra": extra}) and not marker.evaluate({**env, "extra": ""}):
            # Strip the marker: it has done its job, and evaluating it again
            # without the extra would wrongly skip the requirement.
            requirement.marker = None
            selected.append(str(requirement))
    return selected


def _name_from_url(raw: str) -> str | None:
    """Best-effort distribution name of a bare requirement URL."""

    url = raw.strip()
    if "://" not in url:
        return None
    url, _, fragment = url.partition("#")
    for part in fragment.split("&"):
        key, _, value = part.partition("=")
        if key == "egg" and value:
            return value
    segment = url.rstrip("/").rsplit("/", 1)[-1]
    # `repo.git@v1.2` pins a ref; `pkg-1.0.tar.gz` / `.zip` is an archive.
    segment = segment.split("@", 1)[0]
    for suffix in (".git", ".tar.gz", ".zip", ".tgz"):
        segment = segment.removesuffix(suffix)
    return segment or None
