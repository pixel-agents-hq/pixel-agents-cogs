# Pytest scope guard

## The problem this solves

Each cog directory in this repo (`corridor`, `architect`, `pico`, `toolbox`,
`testbench`, `deskutils`, `floorplan`, `pixelagents`, ...) has its own
`conftest.py` that installs a fake `sys.modules["redbot.core"]` / `discord`
stub shaped for that cog specifically. These stubs are **mutually
incompatible** — running more than one cog's test suite in the same
`pytest` process means whichever cog's conftest runs first wins that stub
for the rest of the process. Every other cog's tests then fail against the
wrong stub shapes: `AttributeError`, `isinstance` checks against the wrong
class, assertions on the wrong fake objects — a wall of failures that reads
like a real regression but isn't. `.github/workflows/cogs-quality.yml`
avoids this by running each cog as its own CI job; running two or more
together locally hits the exact same wall.

Without a guard, the failure mode is always the same: a huge,
unrelated-looking failure list gets generated and read before anyone
notices the real cause, and the fix is always "run these separately" —
after already paying for the tokens to generate and read that output. The
guard below turns that into an immediate, one-line rejection *before* the
command ever runs, so the expensive round trip never happens.

## How it works

`.claude/settings.json` registers a **`PreToolUse` hook on the `Bash`
tool**, pointed at `.claude/hooks/check_pytest_scope.py`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 .claude/hooks/check_pytest_scope.py", "timeout": 5 }
        ]
      }
    ]
  }
}
```

`PreToolUse` runs **before** the Bash tool call executes, receiving the
proposed command as JSON on stdin (`{"tool_name": "Bash", "tool_input":
{"command": "..."}}`). If the script decides the command should be
blocked, it writes:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "<one-paragraph explanation + the per-cog commands to run instead>"
  }
}
```

Claude Code denies the tool call and shows `permissionDecisionReason`
directly to the model in place of ever running the command — no test
output, no huge failure list, just the reason and the fix.

Note this is *not* Claude Code's `Stop` hook event (which only fires after
the model finishes a turn — too late to prevent an expensive command from
running). "Stop the bad command" here means `PreToolUse` denying it before
it starts, which is the mechanism that actually achieves "fail immediately"
and "save tokens."

## What gets blocked

`check_pytest_scope.py` only looks at `Bash` tool calls, and only at
commands that contain a `pytest` invocation (bare `pytest`, `python -m
pytest`, `python3 -m pytest`, ...). It discovers "cogs" dynamically each
run — any immediate subdirectory of the repo root with its own
`conftest.py` (directly or under `tests/`) — so adding or removing a cog
never needs a matching edit here.

Two things are blocked:

1. **The pytest command's test-path arguments reference two or more
   different cogs**, e.g. `pytest -q corridor architect` or `pytest -q
   floorplan/tests pixelagents/tests`.
2. **The pytest command has no test-path argument at all**, e.g. bare
   `pytest` or `pytest -q` — this implicitly collects every cog into one
   process, which is the same problem by omission rather than by explicit
   argument.

Everything else is allowed, including:

- A single cog, any subpath within it (`pytest -q corridor`, `pytest -q
  corridor/tests/test_reply_sender.py::TestReplySender::test_foo`).
- A single cog alongside a non-cog target (e.g. `pytest -q corridor
  contracts/tests`) — `contracts/` doesn't install a conflicting stub, so
  there's no real conflict.
- **Separate, chained pytest invocations** — `pytest -q corridor &&
  pytest -q architect` runs each in its own process sequentially, so there
  is no shared-stub conflict; the hook splits on `&&`/`||`/`;` and checks
  each side independently.
- A `-k`/`-m`/other value-taking flag whose *value* happens to look like a
  cog name (e.g. `pytest -q architect -k corridor`) — the script tracks
  which flags consume the next token and doesn't mistake a flag's value
  for a test path.

## If you need to change this

- The known-cogs list is not hardcoded — see `_discover_cogs()` in
  `check_pytest_scope.py` if the detection heuristic (conftest.py presence)
  ever needs to change instead.
- The value-taking-flag list (`_VALUE_FLAGS`) may need a new entry if a
  pytest plugin adds a flag that takes a value and could be confused with
  a path; false positives there would show up as this hook blocking a
  legitimate single-cog command.
- To disable temporarily, remove or comment out the `PreToolUse` entry in
  `.claude/settings.json`, or delete `.claude/hooks/check_pytest_scope.py`.

## Where this lives (and where it doesn't)

This repo's `.claude/` directory is excluded from git via a **local**
`.git/info/exclude` entry (not `.gitignore`, so this doesn't affect other
clones or contributors). That exclusion covers the agent-orchestrator's
own session-tracking hooks (`activity-updater.sh`, `metadata-updater.sh`),
which live in `.claude/settings.local.json`, a Claude Code-recognized
*local* settings file (personal overrides, never meant to be committed).
Claude Code merges hooks from `settings.json` and `settings.local.json`
per event, so both files' hooks are active at once.

`.claude/settings.json`, `.claude/hooks/check_pytest_scope.py`, and this
file are force-added (`git add -f`, bypassing that local exclude) and
committed normally — they're genuinely project-level and belong in the
repo, unlike the orchestrator's own session plumbing.
