"""Ordering cogs by their info.json `required_cogs`.

Red never reads `required_cogs` (see docs/dependency-loading.md), so this is
the only place the declared dependency graph turns into a load/unload order.
Every function takes `requires`: cog name -> the names it requires, for every
cog switchboard can see. Edges to names outside `requires` are unknown
dependencies -- reported, never fatal.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


class DependencyCycleError(ValueError):
    def __init__(self, names: Iterable[str]) -> None:
        self.names = tuple(sorted(names))
        super().__init__("required_cogs form a cycle between: " + ", ".join(self.names))


@dataclass(frozen=True, slots=True)
class Closure:
    names: frozenset[str]
    # dependent -> required names no cog path provides.
    unknown: dict[str, tuple[str, ...]]


def closure(targets: Iterable[str], requires: Mapping[str, Iterable[str]]) -> Closure:
    """`targets` plus everything they transitively require."""

    seen: set[str] = set()
    unknown: dict[str, tuple[str, ...]] = {}
    pending = list(targets)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        deps = tuple(requires.get(name, ()))
        missing = tuple(sorted(dep for dep in deps if dep not in requires))
        if missing:
            unknown[name] = missing
        pending.extend(dep for dep in deps if dep in requires)
    return Closure(names=frozenset(seen), unknown=unknown)


def dependents(targets: Iterable[str], requires: Mapping[str, Iterable[str]]) -> frozenset[str]:
    """Every cog that transitively requires one of `targets` (targets excluded)."""

    reverse: dict[str, set[str]] = {}
    for name, deps in requires.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(name)

    start = set(targets)
    seen: set[str] = set()
    pending = list(start)
    while pending:
        name = pending.pop()
        for dependent in reverse.get(name, ()):
            if dependent not in seen:
                seen.add(dependent)
                pending.append(dependent)
    return frozenset(seen - start)


def load_order(names: Iterable[str], requires: Mapping[str, Iterable[str]]) -> list[str]:
    """Topological order of `names`: every cog after the cogs it requires.

    Ties break alphabetically, so the same set always loads in the same order.
    Only edges between members of `names` count.
    """

    members = set(names)
    indegree = {name: 0 for name in members}
    unlocks: dict[str, list[str]] = {name: [] for name in members}
    for name in members:
        for dep in set(requires.get(name, ())):
            if dep in members and dep != name:
                indegree[name] += 1
                unlocks[dep].append(name)
            elif dep == name:
                raise DependencyCycleError([name])

    ready = [name for name, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for dependent in unlocks[name]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)

    if len(order) != len(members):
        raise DependencyCycleError(members - set(order))
    return order


def unload_order(
    targets: Iterable[str],
    loaded: Iterable[str],
    requires: Mapping[str, Iterable[str]],
) -> list[str]:
    """Loaded `targets` and their loaded dependents, dependents first."""

    target_set = set(targets)
    candidates = (target_set | dependents(target_set, requires)) & set(loaded)
    return list(reversed(load_order(candidates, requires)))
