"""Shared test-time stubs for discord.py / redbot.core.

This is the single source of truth other cogs' conftest.py files import
instead of rolling their own `sys.modules` stubs. Two (or more) packages
each installing their own incompatible stub set into the process-global
`sys.modules` is a real conflict -- whichever conftest.py happens to import
last silently wins for the *entire* pytest session, breaking any cog whose
test collection ran first. Centralizing it here means every cog gets the
same, sufficiently-complete stub set regardless of collection order.

Call `install_stubs()` from a package-root conftest.py, before anything in
that package (including its own `conftest.py` in `tests/`) gets imported.
This module itself must not import discord/redbot -- it's what stubs them.
"""

from __future__ import annotations

import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock

_INSTALLED = False


def _generate_custom_id() -> str:
    """Match discord.py's own default: 16 random bytes as hex (32 chars)."""
    return os.urandom(16).hex()


# --- reusable test-side assertions/helpers -----------------------------
#
# These read state recorded by the `_FakeInteraction` stub below. They live
# at module level (not nested in _install_discord) so tests can import them
# directly: `from ..testing import shown_modal, refreshed_view`.


def shown_modal(interaction: Any) -> Any:
    """The modal instance passed to `interaction.response.send_modal`, if any."""
    return getattr(interaction, "shown_modal", None)


def refreshed_view(interaction: Any) -> Any:
    """The view most recently passed to `edit_message`/`edit_original_response`.

    `None` if the interaction's message/view was never refreshed -- the
    exact bug class this suite guards against, where a mutation lands but
    the panel keeps showing stale state.
    """
    return getattr(interaction, "last_edited_view", None)


def followup_messages(interaction: Any) -> list[Any]:
    """Positional content of every `interaction.followup.send(...)` call."""
    return [
        args[0] if args else kwargs.get("content") for args, kwargs in interaction.followup.sent
    ]


def install_stubs() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    _install_discord()
    _install_redbot()


def _make_stub_module(name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _install_discord() -> None:
    discord = _make_stub_module("discord")
    discord.Member = MagicMock
    discord.Guild = MagicMock
    discord.Role = MagicMock
    discord.Embed = MagicMock
    discord.Message = MagicMock
    discord.Object = MagicMock
    discord.HTTPException = Exception

    class _InteractionResponded(Exception):
        """Mirrors discord.InteractionResponded: the initial response (one
        of send_message/send_modal/defer/edit_message) can only be sent
        once per interaction -- everything after that must go through
        `followup` or `edit_original_response`."""

    discord.InteractionResponded = _InteractionResponded

    class _FakeInteractionResponse:
        def __init__(self, parent: _FakeInteraction) -> None:
            self._parent = parent
            self._responded = False
            self.type: str | None = None

        def is_done(self) -> bool:
            return self._responded

        def _acknowledge(self, response_type: str) -> None:
            if self._responded:
                raise _InteractionResponded(self._parent)
            self._responded = True
            self.type = response_type

        async def send_message(self, *args: object, **kwargs: object) -> None:
            self._acknowledge("message")

        async def send_modal(self, modal: object) -> None:
            self._acknowledge("modal")
            self._parent.shown_modal = modal

        async def defer(self, **kwargs: object) -> None:
            self._acknowledge("defer")

        async def edit_message(self, *args: object, **kwargs: object) -> None:
            self._acknowledge("edit_message")
            self._parent.last_edited_view = kwargs.get("view")

    class _FakeFollowup:
        def __init__(self, parent: _FakeInteraction) -> None:
            self._parent = parent
            self.sent: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def send(self, *args: object, **kwargs: object) -> None:
            if not self._parent.response.is_done():
                raise RuntimeError(
                    "followup.send() called before the interaction was ever "
                    "acknowledged -- Discord would reject this webhook call."
                )
            self.sent.append((args, kwargs))

    class _FakeInteraction:
        def __init__(
            self, guild: object = None, user: object = None, client: object = None
        ) -> None:
            self.guild = guild
            self.user = user or MagicMock()
            self.client = client
            self.response = _FakeInteractionResponse(self)
            self.followup = _FakeFollowup(self)
            self.shown_modal: object | None = None
            self.last_edited_view: object | None = None

        async def edit_original_response(self, *args: object, **kwargs: object) -> None:
            if not self.response.is_done():
                raise RuntimeError(
                    "edit_original_response() called before the interaction was "
                    "ever acknowledged -- there is no 'original response' yet."
                )
            self.last_edited_view = kwargs.get("view")

    discord.Interaction = _FakeInteraction

    def _utcnow() -> object:
        import datetime

        return datetime.datetime.now(datetime.UTC)

    discord_utils = _make_stub_module("discord.utils", utcnow=_utcnow)
    discord.utils = discord_utils
    sys.modules["discord.utils"] = discord_utils

    class _MockModal:
        title: str = ""

        def __init_subclass__(cls, title: str = "", **kwargs: object) -> None:
            cls.title = title
            super().__init_subclass__(**kwargs)

        def __init__(
            self, *args: object, title: str = "", custom_id: str = "", **kwargs: object
        ) -> None:
            self.title = title or type(self).title
            self.custom_id = custom_id or _generate_custom_id()
            self.children: list[Any] = []

        def add_item(self, item: Any) -> _MockModal:
            self.children.append(item)
            return self

        async def on_submit(self, interaction: Any) -> None:
            pass

    class _MockTextInput:
        def __init__(
            self,
            *,
            label: str = "",
            placeholder: str = "",
            required: bool = True,
            default: str = "",
            min_length: int | None = None,
            max_length: int = 4000,
            custom_id: str = "",
            **kwargs: object,
        ) -> None:
            self.label = label
            self.placeholder = placeholder
            self.required = required
            self.default = default
            self.value = default
            self.min_length = min_length
            self.max_length = max_length
            self.custom_id = custom_id or _generate_custom_id()

    class _MockLayoutView:
        def __init__(self, *, timeout: float | None = 180.0) -> None:
            self.timeout = timeout
            self.children: list[Any] = []

        def add_item(self, item: Any) -> _MockLayoutView:
            self.children.append(item)
            return self

    class _MockContainer:
        def __init__(self, *items: Any, **kwargs: object) -> None:
            self.children: list[Any] = list(items)

        def add_item(self, item: Any) -> _MockContainer:
            self.children.append(item)
            return self

    class _MockActionRow:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.children: list[Any] = []

        def add_item(self, item: Any) -> _MockActionRow:
            self.children.append(item)
            return self

    class _MockTextDisplay:
        def __init__(self, content: str = "", **kwargs: object) -> None:
            self.content = content

    class _MockButton:
        def __init__(
            self,
            *,
            label: str = "",
            style: object = None,
            disabled: bool = False,
            custom_id: str = "",
            **kwargs: object,
        ) -> None:
            self.label = label
            self.style = style
            self.disabled = disabled
            self.custom_id = custom_id or _generate_custom_id()
            self.callback: Any = None

    class _MockSelect:
        def __init__(
            self,
            *,
            placeholder: str = "",
            options: list[Any] | None = None,
            custom_id: str = "",
            **kwargs: object,
        ) -> None:
            self.placeholder = placeholder
            self.options = options or []
            self.custom_id = custom_id or _generate_custom_id()
            self.values: list[str] = []
            self.callback: Any = None

    class _MockRoleSelect:
        def __init__(
            self,
            *,
            placeholder: str = "",
            min_values: int = 0,
            max_values: int = 25,
            default_values: list[Any] | None = None,
            custom_id: str = "",
            **kwargs: object,
        ) -> None:
            self.placeholder = placeholder
            self.min_values = min_values
            self.max_values = max_values
            self.default_values = default_values or []
            self.custom_id = custom_id or _generate_custom_id()
            self.values: list[Any] = list(self.default_values)
            self.callback: Any = None

    discord_ui = _make_stub_module("discord.ui")
    discord_ui.Modal = _MockModal
    discord_ui.TextInput = _MockTextInput
    discord_ui.LayoutView = _MockLayoutView
    discord_ui.Container = _MockContainer
    discord_ui.ActionRow = _MockActionRow
    discord_ui.TextDisplay = _MockTextDisplay
    discord_ui.Button = _MockButton
    discord_ui.Select = _MockSelect
    discord_ui.RoleSelect = _MockRoleSelect

    class _MockSection:
        """Section wraps display components plus one `.accessory` (a Button
        or Thumbnail). Must be a real stub, not a bare MagicMock --
        `ui_limits.iter_ui_tree` walks `.accessory` looking for nested
        components, and a bare MagicMock auto-vivifies a brand-new child
        mock on every `.accessory` access, so the walk never revisits the
        same object and its id-based cycle guard never fires: an unbounded
        chain of mocks that OOMs the process instead of terminating."""

        def __init__(self, *items: Any, accessory: Any = None, **kwargs: object) -> None:
            self.children: list[Any] = list(items)
            self.accessory = accessory

        def add_item(self, item: Any) -> _MockSection:
            self.children.append(item)
            return self

    class _MockLabel:
        """The Components V2 replacement for TextInput(label=...). Must be a
        real stub for the same reason as `_MockSection` above -- a bare
        MagicMock's `.component` attribute would auto-vivify infinitely."""

        def __init__(
            self,
            *,
            text: str = "",
            description: str | None = None,
            component: Any = None,
            **kwargs: object,
        ) -> None:
            self.text = text
            self.description = description
            self.component = component

    discord_ui.Section = _MockSection
    discord_ui.Label = _MockLabel
    discord.ui = discord_ui
    sys.modules["discord.ui"] = discord_ui

    discord.SelectOption = lambda **kwargs: types.SimpleNamespace(**kwargs)
    discord.ButtonStyle = types.SimpleNamespace(
        secondary="secondary", primary="primary", link="link", danger="danger"
    )
    discord.Color = types.SimpleNamespace(blurple=lambda: None)
    discord.File = MagicMock

    class _ActivityType:
        playing = 0
        streaming = 1
        listening = 2
        watching = 3
        custom = 4
        competing = 5

    discord.ActivityType = _ActivityType

    discord_app_commands = _make_stub_module(
        "discord.app_commands",
        describe=lambda **kwargs: lambda f: f,
        choices=lambda **kwargs: lambda f: f,
        Choice=lambda **kwargs: MagicMock(**kwargs),
    )
    discord.app_commands = discord_app_commands
    sys.modules["discord.app_commands"] = discord_app_commands

    sys.modules["discord"] = discord


def _install_redbot() -> None:
    class _FakeCogLoadError(RuntimeError):
        pass

    class _FakeConfigValue:
        def __init__(self, data: dict[str, Any], key: str) -> None:
            self._data = data
            self._key = key

        async def __call__(self) -> Any:
            return self._data.get(self._key)

        async def set(self, value: Any) -> None:
            self._data[self._key] = value

    class _FakeGuildConfig:
        def __init__(self, defaults: dict[str, Any]) -> None:
            self._data = {
                key: (value.copy() if isinstance(value, list | dict) else value)
                for key, value in defaults.items()
            }

        def __getattr__(self, name: str) -> _FakeConfigValue:
            return _FakeConfigValue(self._data, name)

    class _FakeConfig:
        def __init__(self) -> None:
            self._guild_defaults: dict[str, Any] = {}
            self._guilds: dict[int, _FakeGuildConfig] = {}
            self._global_data: dict[str, Any] = {}

        @classmethod
        def get_conf(
            cls, cog: object, identifier: int = 0, force_registration: bool = False
        ) -> _FakeConfig:
            return cls()

        def register_guild(self, **defaults: object) -> None:
            self._guild_defaults.update(defaults)

        def guild_from_id(self, guild_id: int) -> _FakeGuildConfig:
            if guild_id not in self._guilds:
                self._guilds[guild_id] = _FakeGuildConfig(self._guild_defaults)
            return self._guilds[guild_id]

        def register_global(self, **defaults: object) -> None:
            self._global_data.update(defaults)

        def __getattr__(self, name: str) -> _FakeConfigValue:
            # Only reached for global config keys registered via
            # register_global -- guild_from_id/register_guild above are
            # real attributes, so normal lookup finds them first.
            return _FakeConfigValue(self._global_data, name)

    class _FakeCommand:
        def __init__(self, func: Any) -> None:
            self.callback = func
            self.__wrapped__ = func
            self.__name__ = getattr(func, "__name__", "command")
            self.__doc__ = getattr(func, "__doc__", "")

        async def __call__(self, *args: object, **kwargs: object) -> object:
            return await self.__wrapped__(*args, **kwargs)

        def command(self, **_kwargs: object) -> Any:
            def decorator(func: Any) -> _FakeCommand:
                return _FakeCommand(func)

            return decorator

        def group(self, **_kwargs: object) -> Any:
            def decorator(func: Any) -> _FakeCommand:
                return _FakeCommand(func)

            return decorator

    class _FakeCog:
        @staticmethod
        def listener(func: Any = None) -> Any:
            if func is not None:
                return func

            def decorator(inner: Any) -> Any:
                return inner

            return decorator

    class _FakeCommands:
        Cog = _FakeCog
        Context = object

        @staticmethod
        def guild_only() -> Any:
            def decorator(func: Any) -> Any:
                return func

            return decorator

        @staticmethod
        def admin_or_permissions(**kwargs: object) -> Any:
            # Tags the raw function (not just checked-and-discarded) so a
            # cog's own tests can assert which commands are admin-gated
            # without re-implementing Red's actual check machinery here --
            # see e.g. pico/tests/test_cog_commands.py.
            def decorator(func: Any) -> Any:
                func.__admin_or_permissions__ = kwargs
                return func

            return decorator

        @staticmethod
        def is_owner() -> Any:
            def decorator(func: Any) -> Any:
                func.__is_owner__ = True
                return func

            return decorator

        @staticmethod
        def hybrid_command(**_kwargs: object) -> Any:
            def decorator(func: Any) -> _FakeCommand:
                return _FakeCommand(func)

            return decorator

        @staticmethod
        def hybrid_group(**_kwargs: object) -> Any:
            def decorator(func: Any) -> _FakeCommand:
                return _FakeCommand(func)

            return decorator

    def _make_stub_module(name: str, **attrs: object) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        return module

    redbot = _make_stub_module("redbot")
    redbot_core_errors = _make_stub_module("redbot.core.errors", CogLoadError=_FakeCogLoadError)
    redbot_core = _make_stub_module(
        "redbot.core",
        Config=_FakeConfig,
        commands=_FakeCommands(),
        errors=redbot_core_errors,
    )
    redbot_core_bot = _make_stub_module("redbot.core.bot", Red=object)
    redbot_core_utils = _make_stub_module(
        "redbot.core.utils",
        get_end_user_data_statement_or_raise=lambda _file: "stubbed data statement",
    )

    import tempfile
    from pathlib import Path

    _data_root = Path(tempfile.mkdtemp(prefix="red-testing-data-"))

    def _cog_data_path(cog_instance: object) -> Path:
        path = _data_root / type(cog_instance).__name__
        path.mkdir(parents=True, exist_ok=True)
        return path

    redbot_core_data_manager = _make_stub_module(
        "redbot.core.data_manager", cog_data_path=_cog_data_path
    )
    # `from redbot.core import data_manager` needs this as a real attribute
    # on the redbot.core stub, not just present in sys.modules -- manually
    # inserting a submodule into sys.modules skips the parent-package
    # attribute wiring the real import system would otherwise do.
    redbot_core.data_manager = redbot_core_data_manager

    sys.modules["redbot"] = redbot
    sys.modules["redbot.core"] = redbot_core
    sys.modules["redbot.core.errors"] = redbot_core_errors
    sys.modules["redbot.core.bot"] = redbot_core_bot
    sys.modules["redbot.core.utils"] = redbot_core_utils
    sys.modules["redbot.core.data_manager"] = redbot_core_data_manager
