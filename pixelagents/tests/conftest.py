"""Install stubs before any pixelagents module is imported.

Delegates to corridor's shared stub installer (corridor/testing.py) for
discord/redbot.core instead of rolling a separate one here -- multiple
packages each stubbing sys.modules independently is a real conflict
(whichever conftest.py imports last silently wins for the whole pytest
session), and every generated cog already depends on corridor via
required_cogs. Everything below the `install_stubs()` call is what
pixelagents' tests need beyond that shared baseline: aiohttp faked
entirely (these tests never want a real socket -- contrast cctv/architect,
which bind a real loopback listener) and `cog_data_path` pre-seeded with a
fake webview_dist (mirrors pixelagents/conftest.py's own override, used
for non-test imports of pixelagents such as contracts/pixel_agents/verify.py).
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

from aiohttp import web as _aiohttp_web

# Framework-neutral (zero discord.py/redbot imports), safe to import
# directly regardless of the stub modules this file installs below --
# needed so _FakeRenderedReply.mode matches the real ReplyMode enum
# build_reply_payload (corridor/adapters/api.py) now compares against via
# `is`, not a plain "text"/"embed" string.
from corridor.domain import ReplyMode
from corridor.testing import install_stubs

install_stubs()

import redbot.core.data_manager as _data_manager  # noqa: E402


def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


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


# --- redbot.core.data_manager ---
# Real Red only initializes this once the bot process has started
# (load_basic_configuration); tests construct cogs standalone, so this
# stands in with a throwaway directory per (test-process, cog class name).
# It pre-seeds a webview_dist already matching the packaged vendor pin, so
# constructing a cog in a test never triggers a real clone+build -- see
# infrastructure/webview_build.py's `.built_commit` marker convention.
_FAKE_DATA_ROOT = Path(tempfile.mkdtemp(prefix="pixelagents-test-data-"))
_PIN_COMMIT = (
    (Path(__file__).parents[1] / "infrastructure" / "webview_vendor.commit")
    .read_text(encoding="utf-8")
    .strip()
)


def _fake_cog_data_path(cog_instance: object) -> Path:
    path = _FAKE_DATA_ROOT / type(cog_instance).__name__
    if not path.exists():
        path.mkdir(parents=True)
        webview_dist = path / "webview_dist"
        webview_dist.mkdir()
        (webview_dist / "index.html").write_text("<html><head></head><body></body></html>")
        (webview_dist / ".built_commit").write_text(_PIN_COMMIT + "\n")
    return path


_data_manager.cog_data_path = _fake_cog_data_path


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

    def __init__(self, keyholders=frozenset(), owners=frozenset(), reply_mode="text", default_prefix=";"):
        self._keyholders = keyholders
        self._owners = owners
        self.reply_mode = reply_mode
        self._default_prefix = default_prefix
        self.registered_dependents = set()
        self.capability_checks = []
        self.rendered_replies = []

    def register_dependent(self, extension_name):
        self.registered_dependents.add(extension_name)

    def unregister_dependent(self, extension_name):
        self.registered_dependents.discard(extension_name)

    async def capabilities_satisfy(self, member, group_key):
        member_id = getattr(member, "id", None)
        self.capability_checks.append((member_id, group_key))
        if member_id in self._owners:
            return True
        if group_key == "keyholder":
            return member_id in self._keyholders
        return False

    async def default_prefix(self):
        return self._default_prefix

    async def substitute_default_prefix(self, text):
        return text.replace("[p]", self._default_prefix)

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
        json.dumps(
            {
                "version": 1,
                "cols": 1,
                "rows": 1,
                "layoutRevision": 1,
                "tiles": [255],
                "furniture": [],
            }
        ),
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
