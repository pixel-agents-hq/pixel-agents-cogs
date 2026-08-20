# pixelagents

Vendors and builds the [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)
webview for [`floorplan`](../floorplan) to serve.

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
  and the `webview_bundle_status()` cross-cog surface floorplan consumes.
- [`docs/contract-testing.md`](../docs/contract-testing.md) — how the
  pinned Pixel Agents commit is verified in CI, across both this cog's
  build and floorplan's `WebviewAssetProvider`.
