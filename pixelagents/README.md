# pixelagents

Vendors and builds the [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)
webview for other cogs — [`floorplan`](../floorplan) today — to serve.

`pixelagents` clones the pinned Pixel Agents commit and builds its webview
with `npm`/`vite` into Red's per-cog data directory, the first time
`cog_load` runs and on demand via `[p]pixelagents webview rebuild`. It owns
nothing else — no dashboard, no Discord presence mirroring, no WebSocket
protocol, no Pixel Index integration. `floorplan` depends on this cog
(`required_cogs`) and reads the build's path/status through
`PixelAgents.webview_bundle_status()`; it never triggers a build itself.

Requires `git`, `node`, and `npm` on the bot host — see
[Architecture.md](Architecture.md#building-webview_dist) if the build fails
or a tool is missing. The cog stays loadable either way and the bot owner
gets a DM. [`toolbox`](../toolbox) can install Node.js/npm on the host if
they're missing.

All commands are bot-owner only (`@commands.is_owner()`), the same
reasoning [`toolbox`](../toolbox) uses: the built webview is one shared
artifact on the host, not per-guild data, so a guild-scoped permission tier
would be the wrong fit regardless of how it's granted.

## Why a separate cog just for this

Before [issue #21](https://github.com/pixel-agents-hq/pixel-agents-cogs/issues/21),
vendoring, building, *and* serving the webview all lived in one Cog.
Splitting the build out into its own cog isn't just tidiness — it's what
makes the build reusable:

- **pixelagents owns the distribution, not any one consumer of it.** It
  clones upstream, runs `npm`/`vite`, and produces one `webview_dist` —
  full stop. It has no idea who's going to serve that bundle, and it
  doesn't need to.
- **Any cog can depend on pixelagents and serve the same build.**
  `floorplan` is the first consumer, not a privileged one. A future cog
  could declare `pixelagents` in `required_cogs`, read
  `PixelAgents.webview_bundle_status()` for the build's path, and serve the
  exact same files under its own Dashboard route — no pixelagents code
  change, no second build, no coordination beyond that one read-only call.
  This is also why the build itself is asset-URL-relative rather than
  rooted at any specific cog's route (see
  [Architecture.md](Architecture.md#building-webview_dist)): a bundle baked
  for one consumer's URL couldn't be correctly served by a second one.
- **One build, one place to update.** When the pinned commit moves —
  `[p]pixelagents webview setcommit`, or the daily `vendor-update.yml`
  bump — every cog serving that build picks up the change the moment it
  next reads `webview_bundle_status()`. There's no per-consumer build to
  fall out of sync, and no risk of two cogs quietly serving two different
  commits of the same office.
- **A build failure (missing `git`/`node`/`npm`, a broken upstream commit)
  is one problem in one place**, diagnosed via `webview_bundle_status()`
  and retried with `[p]pixelagents webview rebuild` regardless of how many
  cogs are waiting on the result — not a failure mode every consumer has to
  reimplement its own handling for.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`), purely
for reply-formatting consistency — pixelagents holds no permission checks of
its own:

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs pixelagents
[p]load pixelagents
```

## Commands

| Command | Description |
|---|---|
| `[p]pixelagents webview commit` | Show which Pixel Agents commit the webview builds from |
| `[p]pixelagents webview setcommit <commit>` | Pin webview builds to a specific commit or link |
| `[p]pixelagents webview resetcommit` | Revert to the source-pinned default commit |
| `[p]pixelagents webview rebuild` | Re-clone and rebuild the webview now |

## Docs

- [Architecture.md](Architecture.md) — the vendor pin, the build pipeline,
  and the `webview_bundle_status()` cross-cog surface any consuming cog reads.
- [`docs/contract-testing.md`](../docs/contract-testing.md) — how the
  pinned Pixel Agents commit is verified in CI, across both this cog's
  build and floorplan's `WebviewAssetProvider`.
