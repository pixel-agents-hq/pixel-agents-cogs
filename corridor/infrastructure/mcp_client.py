"""Corridor's MCP client -- talks to any registered `RegisteredMcpServer`
(suggestionbox's own server today, see docs/suggestionbox-design.md) over
MCP's Streamable HTTP transport, using the official `mcp` SDK.

Pinned `mcp>=1.29,<2` in `requirements/pyproject.toml`: `mcp` 2.0 renamed
`FastMCP` to `MCPServer`, reshuffled several client-side modules, and needs
`pydantic>=2.12`, which the meta-package's `pydantic<2.12` ceiling rules
out. `mcp<2` keeps the API this module (and `suggestionbox`'s own server)
is written against.

The ceilings on `pydantic`, `typing-inspection` and `anyio` exist because
Red pins `typing_extensions==4.13.2` in site-packages, ahead of
Downloader's lib dir on `sys.path`, so a newer copy pip installs there is
shadowed: pydantic 2.12's `pydantic_core` imports
`typing_extensions.Sentinel`, typing-inspection 0.4.3 needs
`typing_extensions>=4.15`, and anyio 4.15 imports the lowercase
`typing_extensions.sentinel` (4.16+). `mcp` (through `pydantic-settings`)
and `httpx` declare `pydantic`/`anyio` without an upper bound, and
Downloader runs one `pip install -U` per requirement string, so the pins
hold only because every cog lists the single `pixel-agents-deps`
requirement and everything resolves in one pip run
(`requirements/README.md`). `corridor/requirement_hint.py` names the fix
when one breaks anyway.

Opens a fresh `streamable_http_client`/`ClientSession` pair per call rather
than holding one open across calls -- unlike `LiteLLMClient`'s one
reusable `aiohttp.ClientSession` (reused because pico/architect's chat
completions are frequent, latency-sensitive traffic), a registered
server's tools are called rarely (an agent reporting one error), so the
extra connection-setup cost is a good trade for never needing reconnect-
on-drop or session-id bookkeeping across arbitrarily long idle gaps.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp import types as mcp_types
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger("red.pixel-agents-cogs.corridor")

MAX_MEDIA_BYTES = 8 * 1024 * 1024
MAX_MEDIA_BLOCKS = 8


@dataclass(frozen=True)
class McpToolListing:
    tools: tuple[mcp_types.Tool, ...]
    instructions: str = ""


@dataclass(frozen=True)
class McpImage:
    mime_type: str
    data_base64: str


@dataclass(frozen=True)
class McpResource:
    uri: str
    mime_type: str
    data: bytes


class McpToolResult(dict[str, Any]):
    """JSON-compatible text result with out-of-band media for capable adapters.

    Ordinary dict/JSON consumers never serialize binary payloads into model text or logs.
    """

    def __init__(self, content: Mapping[str, Any]) -> None:
        super().__init__(content)
        self.images: tuple[McpImage, ...] = ()
        self.resources: tuple[McpResource, ...] = ()


class McpRequestError(RuntimeError):
    """Raised on any failure to reach a registered MCP server, or a tool
    call that server reports as an error -- callers (`AgentToolServerRegistry`)
    catch this and fail closed, the same convention `LLMRequestError`
    already sets for corridor's other outbound client, `LiteLLMClient`."""


class McpClientPool:
    """Stateless beyond logging -- one instance shared corridor-wide, since
    there is no per-connection state kept between calls (see module
    docstring on why each call opens its own connection)."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or log

    async def list_tools(
        self, base_url: str, *, headers: Mapping[str, str] | None = None
    ) -> tuple[mcp_types.Tool, ...]:
        return (await self.discover_tools(base_url, headers=headers)).tools

    async def discover_tools(
        self, base_url: str, *, headers: Mapping[str, str] | None = None
    ) -> McpToolListing:
        """Read current instructions and all tool pages within a bounded
        handshake. `headers` (e.g. a server's bearer token) go on every
        request."""

        try:
            async with asyncio.timeout(30):
                async with (
                    _http_client(headers) as http_client,
                    streamable_http_client(base_url, http_client=http_client) as (
                        read,
                        write,
                        _get_session_id,
                    ),
                ):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        tools: list[mcp_types.Tool] = []
                        cursor = None
                        seen: set[str] = set()
                        for _ in range(100):
                            result = await session.list_tools(cursor=cursor)
                            tools.extend(result.tools)
                            cursor = result.nextCursor
                            if not cursor:
                                return McpToolListing(tuple(tools), initialized.instructions or "")
                            if cursor in seen:
                                break
                            seen.add(cursor)
                        raise McpRequestError("MCP tool pagination did not terminate")
        except Exception as exc:
            raise McpRequestError(f"could not list tools from {base_url}: {exc}") from exc

    async def call_tool(
        self,
        base_url: str,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """Calls `name` on the server at `base_url` and returns its result
        as a plain JSON-object-shaped mapping -- `RegisteredTool.handler`'s
        own contract. Raises `McpRequestError` on a connection/protocol
        failure or a result the server itself flagged as an error
        (`CallToolResult.isError`).

        `timeout_seconds`, when given, bounds how long this call waits for
        a response at the MCP protocol layer (`ClientSession`'s own
        `read_timeout_seconds`) -- needed for a tool that may itself block
        server-side well past a normal reply (e.g. a registered server's
        own `wait_for_job`-shaped tool).

        Deliberately not an `httpx`-level timeout: a Streamable HTTP
        session multiplexes a tool call's request and its eventual
        response over a long-lived SSE connection via `anyio` memory
        streams, so `httpx`'s own per-connection timeout never actually
        bounds how long one specific call waits for its result --
        `ClientSession`/`BaseSession.send_request` has *no* timeout at all
        here otherwise (confirmed by reproducing the hang locally: a short
        `httpx` client timeout alone left a slow tool call waiting
        indefinitely, since `send_request`'s own `anyio.fail_after(timeout)`
        only fires when a `read_timeout_seconds` was actually supplied,
        here or on `ClientSession` itself -- neither was, before this
        parameter existed). `None` waits indefinitely, unchanged from
        before this parameter existed."""

        read_timeout = timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None
        try:
            async with (
                _http_client(headers) as http_client,
                streamable_http_client(base_url, http_client=http_client) as (
                    read,
                    write,
                    _get_session_id,
                ),
            ):
                async with ClientSession(read, write, read_timeout_seconds=read_timeout) as session:
                    await session.initialize()
                    result = await session.call_tool(name, dict(arguments))
        except Exception as exc:
            raise McpRequestError(f"could not call tool {name!r} on {base_url}: {exc}") from exc

        if result.isError:
            raise McpRequestError(
                f"tool {name!r} on {base_url} returned an error: {_result_text(result)}"
            )
        return _result_to_mapping(result)


def _http_client(headers: Mapping[str, str] | None) -> httpx.AsyncClient:
    """The client one MCP exchange runs on: the MCP transports' own default
    timeouts (30 s, 300 s to read a held-open response stream), plus
    `headers` on every request."""

    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0), headers=dict(headers or {}))


def _result_text(result: mcp_types.CallToolResult) -> str:
    texts = [block.text for block in result.content if isinstance(block, mcp_types.TextContent)]
    return "\n".join(texts) if texts else "(no text content)"


def _result_to_mapping(result: mcp_types.CallToolResult) -> McpToolResult:
    texts = [block.text for block in result.content if isinstance(block, mcp_types.TextContent)]
    mapped = McpToolResult(
        result.structuredContent
        if result.structuredContent is not None
        else {"text": "\n".join(texts)}
        if texts
        else {}
    )
    images: list[McpImage] = []
    resources: list[McpResource] = []
    warnings: list[str] = []
    remaining = MAX_MEDIA_BYTES
    for block in result.content:
        if not isinstance(block, (mcp_types.ImageContent, mcp_types.EmbeddedResource)):
            continue
        if len(images) + len(resources) >= MAX_MEDIA_BLOCKS:
            warnings.append("Media block limit exceeded; request fewer images/resources.")
            break
        try:
            if isinstance(block, mcp_types.ImageContent):
                if block.mimeType not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    raise ValueError("Unsupported image MIME type")
                data = _decode_media(block.data, remaining)
                images.append(McpImage(block.mimeType, block.data))
            else:
                resource = block.resource
                if isinstance(resource, mcp_types.BlobResourceContents):
                    data = _decode_media(resource.blob, remaining)
                else:
                    if len(resource.text) > remaining:
                        raise ValueError("Media byte limit exceeded")
                    data = resource.text.encode("utf-8")
                    if len(data) > remaining:
                        raise ValueError("Media byte limit exceeded")
                resources.append(
                    McpResource(
                        str(resource.uri), resource.mimeType or "application/octet-stream", data
                    )
                )
            remaining -= len(data)
        except (ValueError, binascii.Error) as exc:
            warnings.append(f"Media omitted: {exc}. Request a smaller preview or artifact chunk.")
    mapped.images = tuple(images)
    mapped.resources = tuple(resources)
    if images or resources or warnings:
        mapped["mcp_media"] = {
            "image_count": len(images),
            "resources": [
                {"uri": r.uri, "mime_type": r.mime_type, "size_bytes": len(r.data)}
                for r in resources
            ],
            "warnings": warnings,
        }
    return mapped


def _decode_media(encoded: str, remaining: int) -> bytes:
    if len(encoded) > 4 * ((remaining + 2) // 3):
        raise ValueError("Media byte limit exceeded")
    data = base64.b64decode(encoded, validate=True)
    if len(data) > remaining:
        raise ValueError("Media byte limit exceeded")
    return data


__all__ = ["McpClientPool", "McpRequestError"]
