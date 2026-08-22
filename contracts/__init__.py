async def setup(bot: object) -> None:
    """No-op. `contracts` is CI-only (see README.md) and is never meant to be
    `[p]load`ed as a real cog -- this exists solely because dev-time hot
    reload tooling (e.g. d-cogs' HotReload) infers "this is a reloadable cog"
    from *any* top-level package that has an `info.json`, without checking
    its `"type"`/`"hidden"` fields the way Red's own Downloader does. Without
    a `setup` function, Red's extension loader raises `ClientException:
    extension contracts does not have a setup function` on every reload
    attempt HotReload makes after any file changes anywhere under
    `contracts/`, which HotReload then reports as a failure in Discord.
    `info.json` itself can't be removed to dodge this: it's what tells Red's
    real Downloader (`[p]repo`/`[p]cog install`) to exclude `contracts` from
    the installable cog list in the first place -- removing it would make
    Downloader offer `contracts` as an installable "COG" instead (Red's
    default `type` when `info.json` is absent), which is worse.
    """
