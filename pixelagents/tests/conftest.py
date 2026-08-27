"""Install stubs before any pixelagents module is imported."""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

from aiohttp import web as _aiohttp_web

# Framework-neutral (zero discord.py/redbot imports), safe to import
# directly regardless of the stub modules this file installs below --
# needed so _FakeRenderedReply.mode matches the real ReplyMode enum
# build_reply_payload (corridor/adapters/api.py) now compares against via
# `is`, not a plain "text"/"embed" string.
from corridor.domain import ReplyMode


def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# --- discord ---
_discord = _make_stub_module("discord")
_discord.Member = MagicMock
_discord.Guild = MagicMock
_discord.Color = MagicMock(blurple=MagicMock(return_value=None))
_discord.Embed = MagicMock
_discord.HTTPException = Exception
_discord.Message = MagicMock
_discord.File = MagicMock


class _ActivityType:
    playing = 0
    streaming = 1
    listening = 2
    watching = 3
    custom = 4
    competing = 5


_discord.ActivityType = _ActivityType
_discord.Object = MagicMock
_discord.Role = MagicMock


# discord.Interaction stub
class _FakeInteractionResponse:
    def __init__(self):
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, *args, **kwargs):
        self._done = True

    async def send_modal(self, modal):
        self._done = True

    async def defer(self, **kwargs):
        self._done = True


class _FakeInteractionFollowup:
    async def send(self, *args, **kwargs):
        pass


class _FakeInteraction:
    def __init__(self, guild=None, user=None, client=None):
        self.guild = guild
        self.user = user or MagicMock()
        self.client = client
        self.response = _FakeInteractionResponse()
        self.followup = _FakeInteractionFollowup()


_discord.Interaction = _FakeInteraction


def _utcnow():
    import datetime

    return datetime.datetime.now(datetime.UTC)


_discord_utils = _make_stub_module("discord.utils", utcnow=_utcnow)
_discord.utils = _discord_utils
sys.modules["discord.utils"] = _discord_utils


def _gen_custom_id() -> str:
    """Match discord.py's own default: 16 random bytes as hex (32 chars)."""
    import os

    return os.urandom(16).hex()


# discord.ui stub
class _MockModal:
    title: str = ""

    def __init_subclass__(cls, title: str = "", **kwargs):
        cls.title = title
        super().__init_subclass__(**kwargs)

    def __init__(self, *args, custom_id: str = "", **kwargs):
        self.timeout = kwargs.get("timeout")
        self.custom_id = custom_id or _gen_custom_id()
        self.children = []

    def add_item(self, item):
        self.children.append(item)
        return self

    async def on_submit(self, interaction):
        pass

    async def on_error(self, interaction, error):
        pass


class _MockTextInput:
    def __init__(self, *, label: str = "", placeholder: str = "", required: bool = True,
                 min_length: int = 0, max_length: int = 4000, custom_id: str = "", **kwargs):
        self.label = label
        self.placeholder = placeholder
        self.required = required
        self.min_length = min_length
        self.max_length = max_length
        self.default = kwargs.get("default", "")
        self.value = self.default
        self.custom_id = custom_id or _gen_custom_id()

    def __set_name__(self, owner, name):
        self._name = name


class _MockLabel:
    """The Components V2 replacement for TextInput(label=...): wraps a
    single input and carries the label text/description that used to live
    on the input itself. Must be a real stub (not a bare MagicMock) or its
    text/description are unverifiable auto-mock attributes."""

    def __init__(self, *, text: str = "", description: str | None = None, component=None, **kwargs):
        self.text = text
        self.description = description
        self.component = component


class _MockLayoutView:
    def __init__(self, *, timeout=180.0):
        self.timeout = timeout
        self.children = []
        self._stopped = False

    def add_item(self, item):
        self.children.append(item)
        return self

    def stop(self):
        self._stopped = True


def _stub_ui_item(*args, **kwargs):
    # A bare MagicMock class can't stand in for these constructors: MagicMock's
    # own __init__ treats a first positional arg as `spec`, which would silently
    # restrict the returned mock's attributes (e.g. dropping `.add_item`).
    return MagicMock()


class _MockSection:
    """Section wraps display components plus one `.accessory` (a Button or
    Thumbnail). Must be a real stub, not `_stub_ui_item`'s bare MagicMock --
    `ui_limits.iter_ui_tree` walks `.accessory` looking for nested
    components, and a bare MagicMock auto-vivifies a brand-new child mock on
    every `.accessory` access, so the walk never revisits the same object
    and its id-based cycle guard never fires -- an unbounded chain of mocks
    that OOMs the process instead of terminating."""

    def __init__(self, *items, accessory=None, **kwargs):
        self.children = list(items)
        self.accessory = accessory

    def add_item(self, item):
        self.children.append(item)
        return self


class _MockLeafItem:
    """Thumbnail/MediaGallery: leaf components with no nested
    accessory/component/children. A bare MagicMock here is unsafe for the
    same reason as `_MockSection` above -- if one ever ends up reachable as
    a Section's `.accessory` or a Container's child, `iter_ui_tree` would
    auto-vivify an infinite `.accessory` chain through it."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _MockContainer:
    # Accepts and ignores extra kwargs (e.g. accent_colour) so both this
    # cog's and corridor's construction calls work against one shared stub.
    def __init__(self, *items, **kwargs):
        self.children = list(items)

    def add_item(self, item):
        self.children.append(item)
        return self


class _MockActionRow:
    def __init__(self, *args, **kwargs):
        self.children = []

    def add_item(self, item):
        self.children.append(item)
        return self


class _MockTextDisplay:
    def __init__(self, content="", **kwargs):
        self.content = content


class _MockButton:
    def __init__(self, *, label="", style=None, disabled=False, custom_id="", **kwargs):
        self.label = label
        self.style = style
        self.disabled = disabled
        self.custom_id = custom_id or _gen_custom_id()
        self.callback = None


class _MockSelect:
    def __init__(self, *, placeholder="", options=None, custom_id="", **kwargs):
        self.placeholder = placeholder
        self.options = options or []
        self.custom_id = custom_id or _gen_custom_id()
        self.values = []
        self.callback = None


class _MockRoleSelect:
    def __init__(self, *, placeholder="", min_values=0, max_values=25, default_values=None,
                 custom_id="", **kwargs):
        self.placeholder = placeholder
        self.min_values = min_values
        self.max_values = max_values
        self.default_values = default_values or []
        self.custom_id = custom_id or _gen_custom_id()
        self.values = list(self.default_values)
        self.callback = None


_discord_ui = _make_stub_module("discord.ui")
_discord_ui.Modal = _MockModal
_discord_ui.TextInput = _MockTextInput
_discord_ui.LayoutView = _MockLayoutView
_discord_ui.Label = _MockLabel
_discord_ui.Container = _MockContainer
_discord_ui.Section = _MockSection
_discord_ui.Thumbnail = _MockLeafItem
_discord_ui.TextDisplay = _MockTextDisplay
_discord_ui.MediaGallery = _MockLeafItem
_discord_ui.ActionRow = _MockActionRow
_discord_ui.Select = _MockSelect
_discord_ui.RoleSelect = _MockRoleSelect
_discord_ui.Button = _MockButton
_discord.ui = _discord_ui
sys.modules["discord.ui"] = _discord_ui
_discord.SelectOption = _stub_ui_item
_discord.MediaGalleryItem = _stub_ui_item
_discord.ButtonStyle = MagicMock(secondary=None, primary=None, link=None)


# discord.app_commands stub
_discord_app_commands = _make_stub_module("discord.app_commands")
_discord_app_commands.describe = lambda **kwargs: (lambda f: f)
_discord_app_commands.choices = lambda **kwargs: (lambda f: f)
_discord_app_commands.Choice = lambda **kwargs: MagicMock(**kwargs)
_discord.app_commands = _discord_app_commands
sys.modules["discord.app_commands"] = _discord_app_commands

sys.modules["discord"] = _discord


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


# --- redbot ---
_redbot = _make_stub_module("redbot")
_redbot_core = _make_stub_module("redbot.core")


class _FakeConfigAttr:
    def __init__(self, data, key):
        self._data = data
        self._key = key

    async def __call__(self):
        return self._data.get(self._key)

    async def set(self, value):
        self._data[self._key] = value


class _FakeGuildConfigAttr:
    def __init__(self, data, key):
        self._data = data
        self._key = key

    async def __call__(self):
        return self._data.get(self._key)

    async def set(self, value):
        self._data[self._key] = value


class _FakeGuildConfig:
    def __init__(self, guild_id, data=None):
        self.guild_id = guild_id
        self._data = data if data is not None else {"enabled": False, "include_bots": True}

    def __getattr__(self, name):
        return _FakeGuildConfigAttr(self._data, name)


class _FakeUserConfig:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        return _FakeConfigAttr(self._data, name)


class _FakeConfig:
    _global: dict

    def __init__(self):
        self._global = {}
        self._guild_defaults = {}
        self._guilds = {}
        self._user_defaults = {}
        self._users = {}
        self.identifier = None
        self.force_registration = False

    @classmethod
    def get_conf(cls, cog, identifier=0, force_registration=False, cog_name=None):
        config = cls()
        config.identifier = identifier
        config.cog_name = cog_name
        config.force_registration = force_registration
        return config

    def register_global(self, **defaults):
        for k, v in defaults.items():
            self._global.setdefault(k, v)

    def register_guild(self, **defaults):
        self._guild_defaults.update(defaults)

    def register_user(self, **defaults):
        self._user_defaults.update(defaults)

    def guild(self, guild):
        guild_id = guild.id if hasattr(guild, "id") else guild
        return self.guild_from_id(guild_id)

    def guild_from_id(self, guild_id):
        data = self._guilds.setdefault(
            guild_id,
            {
                key: (value.copy() if isinstance(value, dict) else value)
                for key, value in self._guild_defaults.items()
            },
        )
        return _FakeGuildConfig(guild_id, data)

    def user(self, user):
        user_id = user.id if hasattr(user, "id") else int(user)
        data = self._users.setdefault(
            user_id,
            {key: (value.copy() if isinstance(value, dict) else value) for key, value in self._user_defaults.items()},
        )
        return _FakeUserConfig(data)

    def user_from_id(self, user_id):
        data = self._users.setdefault(
            user_id,
            {key: (value.copy() if isinstance(value, dict) else value) for key, value in self._user_defaults.items()},
        )
        return _FakeUserConfig(data)

    def __getattr__(self, name):
        return _FakeConfigAttr(self._global, name)


class _FakeGroup:
    """Stub for a Red command Group — supports `.command()` sub-decorator."""

    def __init__(self, func, **metadata):
        self.__wrapped__ = func
        self.__name__ = getattr(func, "__name__", "group")
        self.__doc__ = getattr(func, "__doc__", "")
        self.command_metadata = metadata
        self.subcommands = {}

    def __call__(self, *args, **kwargs):
        return self.__wrapped__(*args, **kwargs)

    def command(self, **kw):
        def deco(f):
            name = kw.get("name", getattr(f, "__name__", "command"))
            f.__command_metadata__ = {**kw, "name": name}
            self.subcommands[name] = f
            return f
        return deco

    def group(self, **kw):
        def deco(f):
            name = kw.get("name", getattr(f, "__name__", "group"))
            metadata = {**kw, "name": name}
            group = _FakeGroup(f, **metadata)
            self.subcommands[name] = group
            return group
        return deco


class _FakeListener:
    def __init__(self, func=None):
        self._func = func

    def __call__(self, func):
        return func


class _FakeCog:
    @staticmethod
    def listener(func=None, name=None):
        if func is not None:
            func.__cog_listener__ = True
            func.__cog_listener_names__ = [name or func.__name__]
            return func

        def deco(f):
            f.__cog_listener__ = True
            f.__cog_listener_names__ = [name or f.__name__]
            return f
        return deco


class _FakeCommands:
    Cog = _FakeCog

    class Context:
        interaction = None

    @staticmethod
    def admin_or_permissions(**kw):
        def deco(f):
            f.__permissions__ = kw
            return f
        return deco

    @staticmethod
    def guild_only():
        def deco(f):
            f.__guild_only__ = True
            return f
        return deco

    @staticmethod
    def is_owner():
        def deco(f):
            f.__is_owner__ = True
            return f
        return deco

    @staticmethod
    def group(**kw):
        def deco(f):
            return _FakeGroup(f, **kw)
        return deco

    @staticmethod
    def hybrid_group(**kw):
        def deco(f):
            return _FakeGroup(f, **kw)
        return deco

    @staticmethod
    def command(**kw):
        def deco(f):
            f.__command_metadata__ = kw
            return f
        return deco

    @staticmethod
    def hybrid_command(**kw):
        def deco(f):
            f.__command_metadata__ = kw
            return f
        return deco


_redbot_core.Config = _FakeConfig
_redbot_core.commands = _FakeCommands()
_redbot_core_bot = _make_stub_module("redbot.core.bot")
_redbot_core_bot.Red = object


class _FakeCogLoadError(RuntimeError):
    pass


_redbot_core_errors = _make_stub_module("redbot.core.errors", CogLoadError=_FakeCogLoadError)
_redbot_core.errors = _redbot_core_errors


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


def _fake_cog_data_path(cog_instance=None, raw_name=None):
    name = raw_name or type(cog_instance).__name__
    path = _FAKE_DATA_ROOT / name
    if not path.exists():
        path.mkdir(parents=True)
        webview_dist = path / "webview_dist"
        webview_dist.mkdir()
        (webview_dist / "index.html").write_text("<html><head></head><body></body></html>")
        (webview_dist / ".built_commit").write_text(_PIN_COMMIT + "\n")
    return path


_redbot_core_data_manager = _make_stub_module(
    "redbot.core.data_manager", cog_data_path=_fake_cog_data_path
)
_redbot_core.data_manager = _redbot_core_data_manager

sys.modules["redbot"] = _redbot
sys.modules["redbot.core"] = _redbot_core
sys.modules["redbot.core.bot"] = _redbot_core_bot
sys.modules["redbot.core.errors"] = _redbot_core_errors
sys.modules["redbot.core.data_manager"] = _redbot_core_data_manager


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
