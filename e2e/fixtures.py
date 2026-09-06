"""Shared test doubles and cog-construction helpers for the multi-cog e2e
harness.

The only things mocked anywhere in this suite are the LLM API boundary
(`ScriptedLLM`, standing in for the real LiteLLM proxy) and the Discord
gateway (`FakeBot`, standing in for a live `redbot.core.bot.Red`) -- every
tool architect/painter call, the office layout codec, corridor's Config
and pub/sub, and cctv's serving stack are the real production classes.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast

import redbot.core.data_manager as data_manager
from aiohttp import web

from cctv.cctv import CCTV
from cctv.infrastructure import settings as cctv_settings
from cctv.infrastructure.webview import WEBVIEW_BASE_PATH
from corridor.corridor import Corridor
from corridor.infrastructure.llm_client import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ToolCall,
    ToolCallFunction,
)
from pixelagents.infrastructure import webview_build
from pixelagents.pixelagents import PixelAgents

if TYPE_CHECKING:
    from playwright.async_api import Page

_LOG = logging.getLogger("e2e.fixtures")

AddCleanup = Callable[[Callable[[], object]], object]
AddAsyncCleanup = Callable[[Callable[[], Awaitable[object]]], object]


def real_webview_build_enabled() -> bool:
    return os.environ.get("PIXELAGENTS_REAL_WEBVIEW_BUILD") == "1"


@dataclass
class FakeUser:
    id: int = 1
    name: str = "e2e-harness"
    bot: bool = True


class FakeBot:
    """Minimal `Red` double shared by every real cog constructed in this
    harness.

    `get_cog`/`add_cog` back every cross-cog `ensure_loaded`/
    `ensure_corridor_loaded` call (`corridor/dependency_loader.py`), which
    each short-circuit the instant `get_cog(name)` returns non-`None` --
    so as long as corridor and pixelagents are `add_cog`-ed onto this same
    bot before the cogs that depend on them are constructed and loaded, no
    cog-manager/extension-loading machinery is ever reached, and every
    dependent resolves the real, already-`cog_load()`-ed instance.
    """

    def __init__(self, owner_ids: frozenset[int] = frozenset({1})) -> None:
        self.owner_ids = owner_ids
        self.user = FakeUser()
        self.guilds: list[Any] = []
        self._cogs: dict[str, Any] = {}
        self.owner_notifications: list[str] = []

    def get_guild(self, guild_id: int) -> Any:
        return None

    def add_cog(self, cog: Any) -> None:
        self._cogs[type(cog).__name__] = cog

    def get_cog(self, name: str) -> Any:
        return self._cogs.get(name)

    @property
    def cogs(self) -> dict[str, Any]:
        return dict(self._cogs)

    async def get_valid_prefixes(self) -> list[str]:
        return [";"]

    async def is_owner(self, user: Any) -> bool:
        return getattr(user, "id", None) in self.owner_ids

    async def send_to_owners(self, message: str) -> None:
        self.owner_notifications.append(message)

    async def wait_until_red_ready(self) -> None:
        return

    async def unload_extension(self, name: str) -> None:
        return


class ScriptedLLM:
    """`ToolLoopService`'s LLM client double: `.complete(**kwargs)` returns
    the next scripted response (or raises it, if it's an exception).
    Mirrors architect/tests/test_tool_loop_service.py's own fake -- the
    same "mock only the wire response, run every tool for real" boundary,
    just reused outside that one test module."""

    def __init__(self, responses: list[ChatCompletionResponse | Exception]) -> None:
        self._responses = list(responses)

    async def complete(self, **kwargs: object) -> ChatCompletionResponse:
        del kwargs
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def tool_call_response(
    name: str, arguments: dict[str, object], *, call_id: str = "1"
) -> ChatCompletionResponse:
    """One `ToolLoopService.run()` turn that calls tool `name` with
    `arguments` and nothing else."""

    return ChatCompletionResponse(
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionResponseMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=call_id,
                            function=ToolCallFunction(name=name, arguments=json.dumps(arguments)),
                        )
                    ],
                )
            )
        ]
    )


def final_response(text: str = "done") -> ChatCompletionResponse:
    """The turn that ends a `ToolLoopService.run()` loop: plain content, no
    further tool calls."""

    return ChatCompletionResponse(
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionResponseMessage(
                    role="assistant", content=text, tool_calls=None
                )
            )
        ]
    )


# --- real cog construction ------------------------------------------------

# The true stub installed by corridor.testing.install_stubs() (e2e/conftest.py
# runs it before any e2e module, including this one, is imported) -- captured
# once so _install_pixelagents_cog_data_path can always delegate to the real
# thing for every cog except PixelAgents, no matter how many times a test
# calls construct_core_cogs() in one process.
_ORIGINAL_COG_DATA_PATH = data_manager.cog_data_path


def _install_pixelagents_cog_data_path(webview_dir: Path) -> None:
    """Point `PixelAgentsBase.__init__`'s `data_manager.cog_data_path(self)`
    call at `webview_dir` for `PixelAgents` specifically, leaving every other
    cog's `cog_data_path` untouched. Must run *before* `PixelAgents(bot)` is
    constructed -- `__init__` resolves `cog_data_path` at construction time,
    not lazily. No pre-seeding of fake build artifacts here (contrast
    pixelagents/conftest.py's own override): `cog_load()` alone decides,
    for real, whether `webview_dir` already holds an up-to-date build or
    needs a genuine clone+npm+vite build."""

    def _cog_data_path(cog_instance: object) -> Path:
        if type(cog_instance).__name__ == "PixelAgents":
            return webview_dir
        return cast(Path, _ORIGINAL_COG_DATA_PATH(cog_instance))

    data_manager.cog_data_path = _cog_data_path


def _resolve_webview_dir(add_cleanup: AddCleanup) -> Path:
    """`PIXELAGENTS_E2E_WEBVIEW_CACHE`, if set, points at a stable directory
    so repeat local runs skip rebuilding (`ensure_webview_built` is
    idempotent). Otherwise a fresh throwaway directory per run, matching
    `pixelagents/tests/test_webview_build.py::TestRealWebviewBuild`'s own
    default."""

    cache = os.environ.get("PIXELAGENTS_E2E_WEBVIEW_CACHE")
    if cache:
        return Path(cache)
    build_dir = TemporaryDirectory()
    add_cleanup(build_dir.cleanup)
    return Path(build_dir.name)


@dataclass
class CoreCogs:
    corridor: Corridor
    pixelagents: PixelAgents
    cctv: CCTV


async def construct_core_cogs(
    bot: FakeBot, *, add_cleanup: AddCleanup, add_async_cleanup: AddAsyncCleanup
) -> CoreCogs:
    """Construct and `cog_load()` real `Corridor`, `PixelAgents`, and `CCTV`
    on `bot`, in the order their own `cog_load()`s require (corridor before
    anything that depends on it). `PixelAgents.cog_load()` performs the real
    pin+build itself -- nothing here pre-builds it or patches the cog's
    private state afterward; see `_install_pixelagents_cog_data_path`.

    Callers needing further cogs (e.g. `Architect`) construct those the same
    manual way on top of the returned `CoreCogs`, same as before this helper
    existed."""

    webview_dir = _resolve_webview_dir(add_cleanup)
    _install_pixelagents_cog_data_path(webview_dir)
    # The patch above is a process-global mutation of a module attribute --
    # restore it once this test is done, so a later test in the same
    # process (or a different suite sharing this interpreter) doesn't
    # silently inherit a stale PixelAgents-specific cog_data_path.
    add_cleanup(lambda: setattr(data_manager, "cog_data_path", _ORIGINAL_COG_DATA_PATH))

    corridor = Corridor(bot)
    await corridor.cog_load()
    bot.add_cog(corridor)
    add_async_cleanup(corridor.cog_unload)

    pixelagents = PixelAgents(bot)
    await pixelagents.cog_load()
    bot.add_cog(pixelagents)
    add_async_cleanup(pixelagents.cog_unload)

    # The one assertion that makes "the cog itself did this" a tested fact
    # rather than an implicit side effect of construction order.
    status = pixelagents.webview_bundle_status()
    assert status.ready, (
        f"pixelagents cog_load() did not produce a ready webview bundle: {status.detail}"
    )
    assert status.built_commit == webview_build.pinned_commit()

    # Ephemeral port -- this suite owns its whole process, but a fixed
    # default (3210) would still collide with a second e2e run on the same
    # host.
    cctv_settings.GLOBAL_DEFAULTS["listener_port"] = 0
    cctv = CCTV(bot)
    await cctv.cog_load()
    bot.add_cog(cctv)
    add_async_cleanup(cctv.cog_unload)

    return CoreCogs(corridor=corridor, pixelagents=pixelagents, cctv=cctv)


# --- Playwright-facing serving + frame capture ----------------------------


async def build_frontend_app(cctv_cog: CCTV, server: object) -> web.Application:
    """A tiny, test-only aiohttp app that serves the real page/asset
    responses `cctv.infrastructure.webview.WebviewAssets` already produces
    in production (real Discord Dashboard integration serves these via
    Red's RPC page-provider protocol instead of a plain HTTP route, which
    is out of scope to stand up here) alongside the *real* WebSocket
    handlers `CctvServer` binds in production -- reusing the same bound
    methods, not reimplementing them, so this never drifts from what a
    real client actually talks to."""

    app = web.Application()
    app.router.add_get("/cctv/discord/ws", server.handle_discord)  # type: ignore[attr-defined]
    app.router.add_get("/cctv/editor/ws", server.handle_editor)  # type: ignore[attr-defined]
    app.router.add_get("/cctv/health", server.handle_health)  # type: ignore[attr-defined]

    assets = cctv_cog._assets  # noqa: SLF001

    async def page_handler(request: web.Request) -> web.Response:
        page = request.match_info["page"]
        response = assets.page_response(page)
        if response.get("status") != 0:
            return web.Response(status=503, text=str(response.get("error_message")))
        web_content = cast("dict[str, object]", response["web_content"])
        return web.Response(text=str(web_content["source"]), content_type="text/html")

    async def static_handler(request: web.Request) -> web.Response:
        tail = request.match_info["tail"]
        response = assets.static_response(tail)
        if response.get("status") != 0:
            return web.Response(status=404)
        raw = cast("dict[str, object]", response["raw_response"])
        body = base64.b64decode(cast(str, raw["body_base64"]))
        # `web.Response(content_type=...)` rejects a charset baked into the
        # value (it wants that split out separately), and WebviewAssets'
        # content types (e.g. "text/javascript; charset=utf-8") already
        # carry one -- set the header directly instead of fighting aiohttp's
        # parsing of it.
        return web.Response(body=body, headers={"Content-Type": str(raw["content_type"])})

    app.router.add_get("/e2e/page/{page}", page_handler)
    app.router.add_get(WEBVIEW_BASE_PATH + "{tail:.*}", static_handler)
    return app


async def start_frontend_app(cctv_cog: CCTV, *, add_async_cleanup: AddAsyncCleanup) -> int:
    """Starts `build_frontend_app` on a real ephemeral loopback port and
    returns it. One app/port serves both `/e2e/page/discord` and
    `/e2e/page/editor` (plus both pages' real WebSocket routes), so a test
    needing both pages open at once still only calls this once."""

    server = cctv_cog._server  # noqa: SLF001
    assert server is not None
    runner = web.AppRunner(await build_frontend_app(cctv_cog, server))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    add_async_cleanup(runner.cleanup)
    raw_server = site._server  # noqa: SLF001
    assert raw_server is not None
    return cast(int, raw_server.sockets[0].getsockname()[1])  # type: ignore[attr-defined]


def capture_websocket_frames(page: Page) -> list[dict[str, object]]:
    """Records every JSON-object WebSocket frame `page` receives, from the
    moment this is called. The page's own JS opens the (base-href/shimmed)
    WebSocket itself once it loads -- this just has to be wired up before
    `page.goto(...)` so the connection isn't missed."""

    frames: list[dict[str, object]] = []

    def on_websocket(ws: object) -> None:
        def on_frame(payload: str) -> None:
            try:
                message = json.loads(payload)
            except (TypeError, ValueError):
                return
            if isinstance(message, dict):
                frames.append(message)

        ws.on("framereceived", on_frame)  # type: ignore[attr-defined]

    page.on("websocket", on_websocket)
    return frames


async def wait_for_frame(
    page: Page,
    frames: list[dict[str, object]],
    predicate: Callable[[dict[str, object]], bool],
    *,
    attempts: int = 50,
    interval_ms: int = 100,
) -> dict[str, object] | None:
    """Polls `frames` for one matching `predicate`, sleeping on `page`
    between attempts. Returns `None` (not a timeout error) if nothing
    matched within the budget -- callers asserting non-arrival rely on
    this running the *full* budget rather than raising early."""

    for _ in range(attempts):
        match = next((f for f in frames if predicate(f)), None)
        if match is not None:
            return match
        await page.wait_for_timeout(interval_ms)
    return None


async def wait_for_bootstrap(page: Page, frames: list[dict[str, object]]) -> None:
    """Waits for the real bootstrap `layoutLoaded` broadcast -- sent once
    the page's own JS has opened its shimmed WebSocket and sent its own
    `webviewReady` (`pixelagents/application/office.py::bootstrap_messages`
    always ends with one, for both the discord and editor pipelines) --
    then clears `frames`. A real readiness signal instead of a fixed sleep
    that could be too short on a loaded CI runner."""

    frame = await wait_for_frame(page, frames, lambda f: f.get("type") == "layoutLoaded")
    assert frame is not None, "browser never received the bootstrap layoutLoaded"
    frames.clear()
