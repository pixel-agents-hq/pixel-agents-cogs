# pixel-agents-deps

Not a cog. A package with no code whose dependencies are every third-party
package the cogs import, pins included. Every cog's `info.json` except
`switchboard`'s has exactly one requirement:

```text
pixel-agents-deps @ git+https://github.com/pixel-agents-hq/pixel-agents-cogs@pixel-agents-deps-v1#subdirectory=requirements
```

## Why one package

Red's Downloader collects the requirements of every cog it installs into a
set and runs one `pip install -U --target <lib>` per requirement. A pin such
as `anyio<4.15` only constrains its own pip run: a later run for `mcp` or
`httpx` resolves anyio again and overwrites it. Listing everything in one
distribution puts every pin in the same pip run as everything that pulls
the pinned package in.

The string must be identical in every `info.json`. Downloader dedupes
requirements by string, so a variant (another ref, or extras such as
`pixel-agents-deps[cctv]`) would be a second pip run and bring the problem
back. Every cog except `switchboard` needs corridor, which needs nearly all
of these packages, so one shared list installs nothing a cog would not get
anyway. `switchboard` needs only Red's own `packaging` and keeps
`"requirements": []`.

## Which ref

The requirement points at the tag `pixel-agents-deps-v<version>`, where
`<version>` is `project.version` in `pyproject.toml`:

- An immutable tag makes an install reproducible. A branch such as
  `develop` would give an old cog checkout whatever pins the branch has on
  the day.
- `[p]cog update` reinstalls requirements only for cogs whose files
  changed. Bumping the version changes every `info.json`, so every updated
  cog reinstalls the new pins. A branch ref never changes the string, so a
  change here alone would never reach existing installs.
- The tag exists before the change merges. CI's Downloader test installs
  the requirement from GitHub, so a PR tests exactly what users will get.

`python -m contracts.info_json.lint_info_json` checks that every `info.json`
and the cookiecutter template use the requirement for the current version,
that no `info.json` carries a requirement of its own, and, when the tag is
present locally, that the tag's `pyproject.toml` matches this one.

## Changing a dependency

1. Edit `dependencies` and bump `version` (for example `1` to `2`).
2. Replace the requirement in every `info.json` and in
   `.cookiecutter/cog-cookiecutter/{{cookiecutter.cog_name}}/info.json`,
   and update the `[p]pipinstall` lines in the root `README.md` and
   corridor's `install_msg`. The lint names each one it finds stale.
3. Push the tag `pixel-agents-deps-v<version>` on the PR's head commit
   before CI runs. Until the PR merges, move it if a later commit changes
   this file; once merged, never move it. The tagged commit need not end
   up on `develop` (a squash merge leaves it on the tag only); the lint
   only needs its `pyproject.toml` to match.
