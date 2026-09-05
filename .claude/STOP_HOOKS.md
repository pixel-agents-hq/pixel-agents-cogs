# Stop hooks

This repo registers `PreToolUse` guards on Claude Code's `Bash` tool for
commands with a known-bad, repeatable failure mode in this environment —
one whose fix is always the same rewrite, never a real code change.
Rediscovering that fix by reading the same wall of output a second time is
a slow, repeatable waste; each guard below fails fast with the working
command instead.

## How it works

`.claude/settings.json` registers each guard as its own entry under one
`PreToolUse` hook on the `Bash` tool:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/check_pytest_scope.py\"", "timeout": 5 },
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/check_gh_pr_edit.py\"", "timeout": 5 }
        ]
      }
    ]
  }
}
```

`PreToolUse` runs **before** the Bash tool call executes, receiving the
proposed command as JSON on stdin (`{"tool_name": "Bash", "tool_input":
{"command": "..."}}`). Each script reads that command; if it decides the
command should be blocked, it writes:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "<one-paragraph explanation + the working command to run instead>"
  }
}
```

Claude Code denies the tool call and shows `permissionDecisionReason`
directly to the model in place of ever running the command — no command
output, no huge failure list, just the reason and the fix. All guards are
anchored to `$CLAUDE_PROJECT_DIR`, not the shell's current directory,
since an earlier Bash call in the same session may have `cd`-ed elsewhere.

Note this is *not* Claude Code's `Stop` hook event (which only fires after
the model finishes a turn — too late to prevent an expensive command from
running). "Stop the bad command" here means `PreToolUse` denying it before
it starts, which is the mechanism that actually achieves "fail immediately"
and "save tokens."

## Pytest scope guard (`check_pytest_scope.py`)

### The problem this solves

Every cog directory in this repo (`corridor`, `architect`, `pico`,
`toolbox`, `testbench`, `deskutils`, `floorplan`, `pixelagents`, ...) has
its own `conftest.py`, and all of them delegate to the same shared
installer, `corridor.testing.install_stubs()`, for the base
`sys.modules["redbot.core"]` / `discord` stub. That shared base is not
where the conflict this guard prevents comes from.

The actual source: two cogs' `conftest.py` files layer *additional*,
cog-specific overrides on top of that shared base, and those layers
**are** mutually incompatible with each other and with cogs that need the
real thing. `pixelagents/tests/conftest.py` and
`floorplan/tests/conftest.py` each fake `aiohttp` entirely
(`sys.modules["aiohttp"] = ...`, since neither wants a real socket in its
own unit tests) and `pixelagents/conftest.py`/`tests/conftest.py` also
override `redbot.core.data_manager.cog_data_path` to pre-seed a fake
`webview_dist`. But `corridor`'s suite (and pico's
`test_architect_client.py`) binds a real loopback A2A listener, and
cctv's suite binds a real loopback aiohttp listener — running either of
those in the same `pytest` process as `pixelagents/tests` or
`floorplan/tests` means whichever conftest's `sys.modules["aiohttp"]`
assignment runs last silently wins for the rest of the process, breaking
the other suite's real-network expectations. Every other cog's tests then
fail in ways that read like a real regression but aren't: `AttributeError`,
`isinstance` checks against the wrong class, assertions on the wrong fake
objects. `.github/workflows/cogs-quality.yml` avoids this by running each
cog as its own CI job; running two or more together locally hits the same
wall.

This guard is deliberately not scoped down to "only pixelagents/floorplan
plus a real-network cog" — it blocks *any* two-cog combination, uniformly,
because nothing here guarantees a future cog's `conftest.py` won't add its
own conflicting layer the same way, and the cost of the guard (rejecting
an uncommon combined-cog invocation) is far lower than the cost of
re-deriving "these aren't real failures" from a wall of unrelated-looking
output. `e2e/` proves multiple real cogs *can* run together in one
process, but it does so by bypassing every cog's own `conftest.py`
entirely — its own `e2e/conftest.py` calls `install_stubs()` directly and
imports cog classes straight from their modules, so `pixelagents/tests/
conftest.py`'s aiohttp-faking and `cog_data_path` override never load in
the first place. That's a hand-authored exception with its own carefully
chosen (non-faking) overrides, not evidence this guard can be dropped for
ordinary per-cog test invocations.

### What gets blocked

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

### If you need to change this

- The known-cogs list is not hardcoded — see `_discover_cogs()` in
  `check_pytest_scope.py` if the detection heuristic (conftest.py presence)
  ever needs to change instead.
- The value-taking-flag list (`_VALUE_FLAGS`) may need a new entry if a
  pytest plugin adds a flag that takes a value and could be confused with
  a path; false positives there would show up as this hook blocking a
  legitimate single-cog command.
- To disable temporarily, remove or comment out its entry in
  `.claude/settings.json`, or delete `.claude/hooks/check_pytest_scope.py`.

## `gh pr edit` guard (`check_gh_pr_edit.py`)

### The problem this solves

This environment's `gh` token is scoped to `read:user`, `repo`,
`user:email`, `workflow`, and `write:packages` — it lacks `read:org` and
`read:discussion`. `gh pr edit`'s underlying GraphQL mutation resolves a
few fields (the acting user's `login`, an org's `name`, a discussion
`slug`) on the way to the actual edit, even though the edit itself needs
neither scope, so the whole call fails with a wall of "Your token has not
been granted the required scopes..." text — every time, regardless of
which PR or which fields are being edited. The fix is always the same:
use the REST API directly instead, which never reaches that GraphQL path:

```sh
gh api repos/<owner>/<repo>/pulls/<number> -X PATCH \
  -f title="..." -f body="..."
```

### What gets blocked

`check_gh_pr_edit.py` looks at `Bash` tool calls whose command invokes
`gh pr edit` (tolerating `gh`'s own global flags like `--repo`/`-R`
before the subcommand). It extracts the PR number/URL/branch argument if
one was given, and substitutes it directly into the suggested `gh api`
command so the fix is copy-pasteable, not just a template. Everything
else — `gh pr view`, `gh pr create`, `gh pr comment`, chained commands
where only one side is `gh pr edit` — is unaffected.

### If you need to change this

- If this token's scopes ever change to include `read:org`/
  `read:discussion`, `gh pr edit` would work again and this guard could be
  deleted along with `check_gh_pr_edit.py`.
- `_PR_EDIT_VALUE_FLAGS` may need a new entry if a future `gh` version adds
  a value-taking flag to `pr edit`; a missing entry would show up as the
  suggested command's PR reference being wrong (a flag's value mistaken
  for the target) rather than a false block.
- To disable temporarily, remove or comment out its entry in
  `.claude/settings.json`, or delete `.claude/hooks/check_gh_pr_edit.py`.

## Where this lives (and where it doesn't)

This repo's `.claude/` directory is excluded from git via a **local**
`.git/info/exclude` entry (not `.gitignore`, so this doesn't affect other
clones or contributors). That exclusion covers the agent-orchestrator's
own session-tracking hooks (`activity-updater.sh`, `metadata-updater.sh`),
which live in `.claude/settings.local.json`, a Claude Code-recognized
*local* settings file (personal overrides, never meant to be committed).
Claude Code merges hooks from `settings.json` and `settings.local.json`
per event, so both files' hooks are active at once.

`.claude/settings.json`, `.claude/hooks/check_pytest_scope.py`,
`.claude/hooks/check_gh_pr_edit.py`, and this file are force-added
(`git add -f`, bypassing that local exclude) and committed normally —
they're genuinely project-level and belong in the repo, unlike the
orchestrator's own session plumbing.
