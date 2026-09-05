"""Install stubs before any floorplan module is imported.

Delegates to corridor's shared stub installer (corridor/testing.py) for
discord/redbot.core instead of rolling a separate one here -- multiple
packages each stubbing sys.modules independently is a real conflict
(whichever conftest.py imports last silently wins for the whole pytest
session), and every generated cog already depends on corridor via
required_cogs. The only thing floorplan's tests need beyond that shared
baseline is aiohttp faked entirely (these tests never want a real socket)
and `make_ctx`, a `commands.Context` double every reply test uses.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web as _aiohttp_web

# Framework-neutral (zero discord.py/redbot imports), safe to import
# directly regardless of the stub modules this file installs below --
# needed so _FakeRenderedReply.mode matches the real ReplyMode enum
# build_reply_payload (corridor/adapters/api.py) now compares against via
# `is`, not a plain "text"/"embed" string.
from corridor.domain import ReplyMode
from corridor.testing import install_stubs

install_stubs()

import discord as _discord  # noqa: E402
import redbot.core as _redbot_core  # noqa: E402

# Re-exported for tests that instantiate the shared stub's Interaction/Config
# doubles directly (`floorplan.tests.conftest.{_FakeInteraction,_FakeConfig}`)
# rather than going through discord/redbot.core themselves.
_FakeInteraction = _discord.Interaction
_FakeConfig = _redbot_core.Config


def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def make_ctx(**overrides: object) -> MagicMock:
    """A `commands.Context` double pre-configured the way every reply test
    needs: a non-interaction command invocation with an awaitable `.send`
    and a real `.clean_prefix` string.

    `MagicMock()` auto-vivifies any attribute access into another
    MagicMock, so a bare `MagicMock()` used directly as `ctx` gives
    `.clean_prefix` a MagicMock, not a string -- corridor's `[p]`
    substitution (`str.replace`) then crashes on it. Centralizing that one
    fixed attribute here, instead of every test setting it individually,
    is the only reason this helper exists. Pass keyword overrides for
    anything a specific test needs to differ, e.g. `make_ctx(interaction=...)`.
    """

    ctx = MagicMock()
    ctx.interaction = None
    ctx.send = AsyncMock()
    ctx.clean_prefix = ";"
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


# --- aiohttp ---
class _WSMsgType:
    TEXT = 1
    BINARY = 2
    PING = 9
    PONG = 10
    CLOSE = 8
    CLOSED = 256
    ERROR = 257


class _FakeWSMessage:
    def __init__(self, data: str = "", msg_type: int = 1):
        self.type = msg_type
        self.data = data


class _FakeClientWebSocketResponse:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.closed = False
        self._sent: list = []

    async def send_str(self, data: str) -> None:
        self._sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            self.closed = True
            raise StopAsyncIteration
        return self._messages.pop(0)


class _FakeClientSession:
    def __init__(self, ws_response=None, timeout=None, **kwargs):
        self._ws_response = ws_response or _FakeClientWebSocketResponse()
        self.closed = False

    async def ws_connect(self, url: str, **kwargs):
        return self._ws_response

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


class _FakeClientError(Exception):
    pass


class _FakeContentTypeError(_FakeClientError):
    pass


_aiohttp = _make_stub_module(
    "aiohttp",
    ClientError=_FakeClientError,
    ClientSession=_FakeClientSession,
    ContentTypeError=_FakeContentTypeError,
    ClientWebSocketResponse=_FakeClientWebSocketResponse,
    WSMsgType=_WSMsgType,
    ClientTimeout=lambda **kwargs: kwargs,
    web=_aiohttp_web,
)
sys.modules["aiohttp"] = _aiohttp
sys.modules["aiohttp.web"] = _aiohttp_web


class _FakeRenderedReply:
    """Test double for corridor's RenderedReply DTO -- pixelagents' ReplyMixin
    only reads these attributes, duck-typed the same way it reads FakeCorridor
    itself (no static import of corridor's domain types, `fields` aside --
    ReplyMixin imports the real corridor.domain.ReplyField as its own field
    type, so this double's `fields` are real ReplyField instances too)."""

    def __init__(
        self,
        *,
        mode,
        content=None,
        embed_title=None,
        embed_description=None,
        fields=(),
        footer_text=None,
        footer_icon_url=None,
        show_timestamp=False,
        author_name=None,
        author_icon_attachment=None,
        category=None,
        footer_icon_attachment=None,
    ):
        self.mode = mode
        self.content = content
        self.embed_title = embed_title
        self.embed_description = embed_description
        self.fields = fields
        self.footer_text = footer_text
        self.footer_icon_url = footer_icon_url
        self.show_timestamp = show_timestamp
        self.author_name = author_name
        self.author_icon_attachment = author_icon_attachment
        self.category = category
        self.footer_icon_attachment = footer_icon_attachment


class FakeCorridor:
    """Test double for corridor's cross-cog permission + reply-render API.

    `keyholders` and `owners` are member ids treated as satisfying the
    "keyholder" group key / bypassing every check, respectively -- mirroring
    corridor's real capabilities_satisfy(member, group_key) contract.

    `reply_mode` ("text" or "embed") mirrors corridor's real
    ReplyService.render: title/description in, a RenderedReply-shaped object
    out, nothing sent -- see corridor/application/reply_service.py.
    """

    def __init__(self, keyholders=frozenset(), owners=frozenset(), reply_mode="text",
                 allow_employee=True):
        self._keyholders = keyholders
        self._owners = owners
        self._allow_employee = allow_employee
        self.reply_mode = reply_mode
        self.registered_dependents = set()
        self.registered_llm_tools_calls = []
        self.unregistered_tool_owners = []
        self.capability_checks = []
        self.rendered_replies = []
        self.published = []
        self._subscribers = {}

    def register_dependent(self, extension_name):
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name):
        self.registered_dependents.discard(extension_name)

    def register_llm_tools(self, cog, *, owner):
        self.registered_llm_tools_calls.append((cog, owner))

    def unregister_tool_owner(self, owner):
        self.unregistered_tool_owners.append(owner)

    async def publish_event(self, event):
        """Mirrors corridor's real EventBusService.publish: records every
        published event (for listener-level assertions), then actually
        dispatches to any registered subscriber (for end-to-end
        assertions) -- see corridor/application/event_bus_service.py, the
        source of truth this double is kept in sync with."""

        self.published.append(event)
        for _owner, handler in list(self._subscribers.get(type(event), ())):
            await handler(event)

    def subscribe_event(self, event_type, handler, *, owner):
        self._subscribers.setdefault(event_type, []).append((owner, handler))

    def unsubscribe_owner(self, owner):
        for handlers in self._subscribers.values():
            handlers[:] = [(o, h) for o, h in handlers if o != owner]

    async def capabilities_satisfy(self, member, group_key):
        member_id = getattr(member, "id", None)
        self.capability_checks.append((member_id, group_key))
        if member_id in self._owners:
            return True
        if group_key == "employee":
            return self._allow_employee
        if group_key == "keyholder":
            return member_id in self._keyholders
        return False

    async def require_permission(self, ctx, group_key):
        if await self.capabilities_satisfy(ctx.author, group_key):
            return True
        await ctx.send("You don't have permission to do that.")
        return False

    async def render_reply(
        self,
        ctx,
        *,
        title=None,
        description=None,
        content=None,
        fields=(),
        code=(),
        identity=None,
        footer_override=None,
        category=None,
    ):
        """Mirrors corridor's real render_reply, including resolving
        `guild_id`/`prefix` from `ctx` itself (a caller never supplies
        either) and ReplyService.render's `[p]` substitution and
        `code`/`ReplyField.code` fencing -- see corridor/adapters/cog_base.py
        and corridor/application/reply_service.py, the source of truth this
        double is kept in sync with."""

        guild_id = ctx.guild.id
        prefix = ctx.clean_prefix
        self.rendered_replies.append((guild_id, title, description, content, tuple(fields)))

        def subst(text):
            return text.replace("[p]", prefix) if text is not None else None

        def fence(text):
            return f"```\n{text}\n```"

        title = subst(title)
        description = subst(description)
        content = subst(content)
        fields = tuple(
            type(field)(field.name, subst(field.value) or "", field.inline, field.code)
            for field in fields
        )
        code_blocks = tuple(fence(subst(entry)) for entry in code)

        if self.reply_mode == "text":
            base = content or description or title or ""
            lines = [base] if base else []
            lines.extend(code_blocks)
            lines.extend(
                f"**{field.name}:**\n{fence(field.value)}"
                if field.code
                else f"**{field.name}:** {field.value}"
                for field in fields
            )
            text = "\n".join(lines)
            if identity is not None and text:
                text = f"**{identity.owner}:** {text}"
            return _FakeRenderedReply(mode=ReplyMode.TEXT, content=text)

        embed_description = description or content
        if code_blocks:
            block_text = "\n".join(code_blocks)
            embed_description = (
                f"{embed_description}\n\n{block_text}" if embed_description else block_text
            )
        embed_fields = tuple(
            type(field)(field.name, fence(field.value), False, field.code)
            if field.code
            else field
            for field in fields
        )
        if footer_override is not None:
            footer_text = footer_override.name
            footer_icon_url = footer_override.icon_url
        else:
            footer_text = None
            footer_icon_url = None
        return _FakeRenderedReply(
            mode=ReplyMode.EMBED,
            embed_title=title,
            embed_description=embed_description,
            fields=embed_fields,
            footer_text=footer_text,
            footer_icon_url=footer_icon_url,
            author_name=identity.owner if identity is not None else None,
            author_icon_attachment=identity.avatar_filename if identity is not None else None,
            category=category,
        )


class FakePixelAgents:
    """Test double for the cross-cog `bot.get_cog("PixelAgents")` reference.

    Mirrors `pixelagents.adapters.cog_base.WebviewBundleStatus` -- floorplan
    only ever reads this, never triggers a (re)build (see
    `adapters/cog_base.py::_sync_webview_assets`).
    """

    def __init__(
        self,
        *,
        ready=True,
        dist_path=None,
        detail="✅ loaded",
        built_commit="a" * 40,
        built_base_path="./",
    ):
        self.dist_path = dist_path or Path(tempfile.mkdtemp(prefix="fake-pixelagents-dist-"))
        self.ready = ready
        self.detail = detail
        self.built_commit = built_commit if ready else None
        self.built_base_path = built_base_path if ready else None
        self.registered_dependents = set()

    def register_dependent(self, extension_name):
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name):
        self.registered_dependents.discard(extension_name)

    def webview_bundle_status(self):
        return types.SimpleNamespace(
            dist_path=self.dist_path,
            ready=self.ready,
            detail=self.detail,
            built_commit=self.built_commit,
            built_base_path=self.built_base_path,
        )


def write_fake_vite_build(build_out_dir: Path) -> None:
    """Write a minimal Vite build output, shaped like a real one.

    Used by test_webview_build.py and test_webview_dist_build.py to exercise
    webview_build._sync_dist / the WebviewAssetProvider contract without a
    real clone+npm+vite build. Covers only what _sync_dist reads -- index.html
    referencing a hashed JS/CSS pair under the Dashboard subpath,
    furniture-catalog.json / asset-index.json / the layout it points at,
    decoded/*.json, and a font -- plus one raw per-tile PNG folder, so a test
    can assert _sync_dist actually drops the passthrough files real Vite
    output also carries, rather than happening to copy everything.
    """

    assets = build_out_dir / "assets"
    (assets / "decoded").mkdir(parents=True)
    (build_out_dir / "fonts").mkdir(parents=True)
    (assets / "characters").mkdir(parents=True)

    (build_out_dir / "index.html").write_text(
        "<!doctype html><html><head>"
        '<script type="module" '
        'src="./assets/index-abc.js"></script>'
        '<link rel="stylesheet" '
        'href="./assets/index-abc.css">'
        '</head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (assets / "index-abc.js").write_text("console.log('office');", encoding="utf-8")
    (assets / "index-abc.css").write_text("body { margin: 0; }", encoding="utf-8")
    (assets / "characters" / "char_0.png").write_bytes(b"not-a-real-png")

    catalog = [
        {
            "id": "DESK",
            "name": "Desk",
            "category": "furniture",
            "file": "DESK.png",
            "width": 1,
            "height": 1,
            "footprintW": 1,
            "footprintH": 1,
        }
    ]
    (assets / "furniture-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (assets / "asset-index.json").write_text(
        json.dumps(
            {"floors": [], "walls": [], "characters": [], "defaultLayout": "default-layout-1.json"}
        ),
        encoding="utf-8",
    )
    (assets / "default-layout-1.json").write_text(
        json.dumps({"version": 1, "cols": 1, "rows": 1, "layoutRevision": 1, "tiles": [255]}),
        encoding="utf-8",
    )
    decoded = {
        "characters.json": [{"down": [], "up": [], "left": [], "right": []}],
        "floors.json": [[["#ffffff"]]],
        "walls.json": [[[["#ffffff"]]]],
        "carpets.json": [[[["#ffffff"]]]],
        "furniture.json": {"DESK": [["#8F6439"]]},
    }
    for name, data in decoded.items():
        (assets / "decoded" / name).write_text(json.dumps(data), encoding="utf-8")
    (build_out_dir / "fonts" / "Font.ttf").write_bytes(b"\x00\x01\x02\x03")
