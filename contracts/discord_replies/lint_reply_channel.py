#!/usr/bin/env python3
"""Fail if a `commands.Context`-based command handler sends a Discord reply
without going through Corridor's shared `ReplyMode` rendering.

Corridor lets guild admins configure a shared reply style (`ReplyMode.TEXT`
vs `ReplyMode.EMBED`, plus icon/footer/timestamp -- see
`corridor/domain/models.py:ReplyPreferences`) that every dependent cog is
meant to respect. A command handler that calls `ctx.send(...)`,
`ctx.interaction.response.send_message(...)`, or
`ctx.interaction.followup.send(...)` -- directly, or one level removed
through a cog-local reply helper like floorplan's (or pixelagents')
`ReplyMixin._reply` -- without that helper ever consulting
`self._corridor.send_reply(...)` / `render_reply(...)` bypasses the guild's
configured style entirely. That's exactly the bug this script exists to
catch (a prior version of pixelagents' `ReplyMixin` did precisely this,
before the office/dashboard/catalogue half of that Cog split into
floorplan).

Approach: for each cog package, index every function/method definition by
name (`contracts.ast_call_graph.index_functions`), then for each Red command
handler (any function decorated with `.command(...)`, `.group(...)`,
`.hybrid_command(...)`, or `.hybrid_group(...)` -- covers `@commands.command`,
`@commands.hybrid_group`, and `@some_group.command`/`.group` subcommand
registration alike) crawl its reachable call graph
(`contracts.ast_call_graph.crawl_call_graph`): follow `self.<name>(...)`
calls into same-named methods elsewhere in the package (this is what lets it
see through a local reply-dispatch helper, not just the command handler's
own body), and note every `ctx`/`interaction` send call found along the
way. If nothing in that whole reachable subgraph ever calls
`self._corridor.send_reply(...)` / `render_reply(...)`, every raw send found
in it is reported against the command handler that reaches it.

This is a static, name-based approximation, not real dataflow analysis: it
does not verify that a found `self._corridor.render_reply(...)` call
actually produces the exact bytes handed to the send it's excusing --
only that the two appear somewhere in the same reachable subgraph. For the
single-reply-per-command shape every cog here actually uses, that's
enough; a handler that legitimately sends one corridor-rendered reply and
one entirely separate raw one would be a false negative this script can't
catch. That's a real gap, not a claim this script proves correctness --
same spirit as contracts/pixel_index/lint_endpoints.py's own docstring
about what it does and doesn't catch.

Two things are deliberately NOT flagged:

  - `discord.Interaction` component/view callbacks (buttons, selects,
    modals -- e.g. `floorplan/adapters/layout_views.py`,
    `floorplan/adapters/settings_panel.py`,
    `corridor/adapters/settings_ui.py`). These aren't Red command handlers
    (no `.command()`/`.group()` decorator), so the traversal never starts
    from them in the first place -- they're out of scope by construction,
    not by exception list. Mostly ephemeral confirmations/errors, not the
    guild's embed-vs-text reply style.

  - A send whose only content-bearing keyword is `view=` (no `content=`/
    `embed=`) -- a Discord Components V2 message. Components V2 cannot be
    mixed with plain content or an embed (Discord rejects it), so it is
    structurally impossible for these to honor `ReplyMode`; each of
    `corridor/adapters/commands.py`'s `[p]corridorsettings` and
    `floorplan/adapters/admin_commands.py`'s `[p]floorplan settings`
    sends its whole interactive panel this way, deliberately unrendered.

Run: python -m contracts.discord_replies.lint_reply_channel
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

from contracts.ast_call_graph import FunctionDef, crawl_call_graph, index_functions

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every cog package this check applies to. Each is scanned (and its call
# graph walked) independently -- add a new cog here when it's created (the
# cookiecutter template's `commands.py` already follows the compliant
# pattern this check enforces).
COG_PACKAGES = ("corridor", "floorplan", "pico", "pixelagents", "toolbox")

_COMMAND_DECORATOR_ATTRS = {"command", "group", "hybrid_command", "hybrid_group"}
_SEND_ATTRS = {"send", "send_message"}
_SEND_ROOTS = {"ctx", "interaction"}
_CORRIDOR_REPLY_CALLS = {"self._corridor.send_reply", "self._corridor.render_reply"}


def _is_command_handler(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if isinstance(func, ast.Attribute) and func.attr in _COMMAND_DECORATOR_ATTRS:
            return True
    return False


def _is_view_only_send(call: ast.Call) -> bool:
    """True for a Components V2 dispatch: `ctx.send(view=...)` with no
    `content=`/`embed=` alongside it. These can't carry an embed or plain
    text at all (Discord rejects mixing them with Components V2), so
    `ReplyMode` structurally doesn't apply -- see the module docstring."""

    if call.args:
        return False
    keys = {kw.arg for kw in call.keywords if kw.arg is not None}
    return "view" in keys and "content" not in keys and "embed" not in keys


def _reachable_raw_sends(
    entry: FunctionDef, index: dict[str, list[FunctionDef]]
) -> tuple[list[tuple[Path, int, str]], bool]:
    """Crawl `entry`'s reachable call graph, returning every raw
    ctx/interaction send found plus whether corridor's reply renderer was
    consulted anywhere in that same reachable subgraph."""

    raw_sends: list[tuple[Path, int, str]] = []
    corridor_consulted = False

    def on_call(caller: FunctionDef, call: ast.Call, dotted: str) -> None:
        nonlocal corridor_consulted
        if dotted in _CORRIDOR_REPLY_CALLS:
            corridor_consulted = True
            return
        attr = dotted.rsplit(".", 1)[-1]
        root = dotted.split(".", 1)[0]
        if attr in _SEND_ATTRS and root in _SEND_ROOTS and not _is_view_only_send(call):
            raw_sends.append((caller.path, call.lineno, dotted))

    def follow(dotted: str) -> bool:
        return dotted.startswith("self.") and dotted not in _CORRIDOR_REPLY_CALLS

    crawl_call_graph(entry, index, follow=follow, on_call=on_call)
    return raw_sends, corridor_consulted


def find_violations(package: str) -> list[tuple[Path, int, str, str]]:
    """Return (send_path, lineno, handler_name, dotted_call) for every
    command handler in `package` that reaches a raw send without ever
    reaching a corridor reply-render call."""

    root = REPO_ROOT / package
    index = index_functions(root)
    violations: list[tuple[Path, int, str, str]] = []
    for defs in index.values():
        for entry in defs:
            if not _is_command_handler(entry.node):
                continue
            raw_sends, corridor_consulted = _reachable_raw_sends(entry, index)
            if corridor_consulted:
                continue
            violations.extend(
                (send_path, lineno, entry.node.name, dotted)
                for send_path, lineno, dotted in raw_sends
            )
    return violations


def main() -> int:
    violations_by_file: dict[Path, list[tuple[int, str, str]]] = defaultdict(list)
    for package in COG_PACKAGES:
        for send_path, lineno, handler_name, dotted in find_violations(package):
            violations_by_file[send_path].append((lineno, handler_name, dotted))

    if violations_by_file:
        print(
            "Command handler(s) send a Discord reply without going through Corridor's "
            "shared ReplyMode rendering:"
        )
        for path, violations in sorted(violations_by_file.items()):
            rel = path.relative_to(REPO_ROOT)
            for lineno, handler_name, dotted in sorted(violations):
                print(f"  {rel}:{lineno} reached from {handler_name}(): {dotted}(...)")
        print(
            "\nRoute the reply through self._corridor.send_reply(...)/render_reply(...) "
            "-- directly, or via a helper that itself does (e.g. pixelagents' "
            "ReplyMixin._reply/_send_public) -- instead of sending directly. A Components "
            "V2 `view=`-only send is exempt; see this script's module docstring."
        )
        return 1

    print(
        "Every command handler in corridor/floorplan/pico/pixelagents/toolbox respects "
        "corridor's ReplyMode."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
