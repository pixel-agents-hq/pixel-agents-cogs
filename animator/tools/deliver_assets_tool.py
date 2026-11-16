"""Turns a finished pixel-art-mcp render job into real Discord attachments.

This is animator's only native tool -- every other capability (creating a
project, running Blender Python, rendering previews/sprites, polling a job)
comes for free through `AgentToolServerTool`, bridging whatever
`pixel-art-mcp` tools `[p]telephonepole` has registered and enabled for
`animator` (see `adapters/cog_base.py`'s `_mcp_tools`). This tool exists to
provide a single delivery step for both package and preview. MCP also supports
embedded artifact bytes, but a bare `download_url` is not itself a
Discord attachment. That would put a raw internal-network URL
(`http://pixel-art-mcp:8000/artifacts/...`) in front of a Discord user who
has no way to reach it, so text alone isn't a usable answer here.

Deliberately does *not* call `pixel-art-mcp` over MCP itself for the
`get_job` lookup -- it reuses whichever `get_job` `RegisteredTool` corridor
already bridged for this agent (same registry entry `AgentToolServerTool`
wraps), so the same per-agent enable/disable toggle
(`[p]telephonepole agents pixel-art`) and connection settings apply
uniformly. Only the two artifacts' actual bytes are fetched directly, via
a plain HTTP GET on `download_url` -- that's a file download, not an MCP
tool call, and `pixel-art-mcp`'s `download_url` is reachable unauthenticated
from any container on `redstack-network` (see
`projects/nntin-labs/services/pixel-art-mcp-deploy/README.md`'s "Auth
model" section).

A `download_url` is fetched only when it shares the scheme, host and port
of the MCP server `get_job` was bridged from (`RegisteredTool.server_url`),
redirects are not followed, and each download stops at
`MAX_ARTIFACT_BYTES`: the bytes are posted to Discord, so a server that
named any other URL would turn the bot into a proxy for whatever it can
reach.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from corridor.domain import RegisteredTool

from .attachment import Attachment

log = logging.getLogger("red.pixel-agents-cogs.animator")

_ZIP_FILENAME = "pixel-agents.zip"
# Prefer an animated preview when available, otherwise the render_asset contact sheet.
_PREVIEW_FILENAMES = ("preview.gif", "preview.png")
# The most one artifact download may read; larger ones are skipped.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: object) -> tuple[str, str, int] | None:
    """`url`'s (scheme, host, port), or None unless it is http(s)."""

    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return scheme, parts.hostname.lower(), port or _DEFAULT_PORTS[scheme]


class _ArtifactTooLarge(Exception):
    pass


class DeliverPixelAgentsAssetsInput(BaseModel):
    job_id: UUID = Field(
        description=(
            "The job ID of a furniture render_asset call whose status from wait_for_job "
            "is 'succeeded' and whose artifacts include pixel-agents.zip."
        )
    )


class DeliverPixelAgentsAssetsOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "error"]
    message: str
    # Never reaches the LLM: ToolLoopService._execute() serializes this
    # Output with model_dump_json() to build the chat-visible tool result,
    # and `exclude=True` drops this field from that JSON entirely -- see
    # `attachment.py`'s module docstring for the full path raw bytes take
    # instead. Read directly off the Output object (via getattr), never off
    # the serialized text.
    attachments: list[Attachment] = Field(default_factory=list, exclude=True)


class DeliverPixelAgentsAssetsTool:
    name = "deliver_pixel_agents_assets"
    description = (
        "Call this once a furniture render_asset job has succeeded (use wait_for_job). Fetches the "
        "resulting pixel-agents.zip and preview (animated preview.gif, or static "
        "preview.png if the export had no animation) and attaches them to the reply "
        "sent back to Discord. Then send a brief final text reply. A bare download_url "
        "is not reachable by a Discord user."
    )

    def __init__(
        self,
        *,
        get_registered_tools: Callable[[], Awaitable[Sequence[RegisteredTool]]],
        http_client: httpx.AsyncClient,
    ) -> None:
        self._get_registered_tools = get_registered_tools
        self._http_client = http_client

    @property
    def Input(self) -> type[BaseModel]:
        return DeliverPixelAgentsAssetsInput

    @property
    def Output(self) -> type[BaseModel]:
        return DeliverPixelAgentsAssetsOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, DeliverPixelAgentsAssetsInput)

        get_job = await self._find_get_job_tool()
        if get_job is None:
            return DeliverPixelAgentsAssetsOutput(
                status="error",
                message=(
                    "pixel-art-mcp's get_job tool is not currently available to animator -- "
                    "check that [p]telephonepole has it registered and enabled for animator."
                ),
            )

        try:
            job = await get_job.handler(None, {"job_id": str(raw_input.job_id)})
        except Exception as exc:
            log.warning("animator: get_job failed for %s: %s", raw_input.job_id, exc)
            return DeliverPixelAgentsAssetsOutput(
                status="error", message=f"Could not look up job {raw_input.job_id}: {exc}"
            )

        job_status = job.get("status")
        if job_status != "succeeded":
            return DeliverPixelAgentsAssetsOutput(
                status="error",
                message=(
                    f"Job {raw_input.job_id} is not finished yet (status: {job_status!r}). "
                    "Use wait_for_job until it reports 'succeeded', then call this again."
                ),
            )

        raw_artifacts = job.get("artifacts") or []
        artifacts: Sequence[Mapping[str, Any]] = (
            raw_artifacts if isinstance(raw_artifacts, Sequence) else []
        )
        attachments = await self._fetch_wanted_artifacts(artifacts, _origin(get_job.server_url))
        if not attachments:
            found = ", ".join(sorted({str(a.get("filename", "?")) for a in artifacts})) or "none"
            return DeliverPixelAgentsAssetsOutput(
                status="error",
                message=(
                    f"Job {raw_input.job_id} succeeded, but neither pixel-agents.zip nor "
                    f"a preview (preview.gif/preview.png) was among its artifacts "
                    f"(found: {found})."
                ),
            )

        delivered = ", ".join(a.filename for a in attachments)
        return DeliverPixelAgentsAssetsOutput(
            status="ok", message=f"Attached {delivered}.", attachments=attachments
        )

    async def _find_get_job_tool(self) -> RegisteredTool | None:
        for tool in await self._get_registered_tools():
            if tool.name == "get_job":
                return tool
        return None

    async def _fetch_wanted_artifacts(
        self, artifacts: Sequence[Mapping[str, Any]], origin: tuple[str, str, int] | None
    ) -> list[Attachment]:
        by_filename = {str(a.get("filename")): a for a in artifacts}
        preview_name = next((name for name in _PREVIEW_FILENAMES if name in by_filename), None)
        wanted = [
            (name, by_filename[name])
            for name in (_ZIP_FILENAME, preview_name)
            if name is not None and name in by_filename
        ]
        attachments: list[Attachment] = []
        for filename, artifact in wanted:
            download_url = artifact.get("download_url")
            if not download_url:
                continue
            if origin is None or _origin(download_url) != origin:
                log.warning(
                    "animator: refusing artifact %s: %r is not on the MCP server's origin",
                    filename,
                    download_url,
                )
                continue
            try:
                data = await self._download(str(download_url))
            except (httpx.HTTPError, _ArtifactTooLarge) as exc:
                log.warning("animator: could not download artifact %s: %s", filename, exc)
                continue
            media_type = artifact.get("media_type") or "application/octet-stream"
            attachments.append(Attachment(filename=filename, media_type=media_type, data=data))
        return attachments

    async def _download(self, url: str) -> bytes:
        chunks: list[bytes] = []
        received = 0
        async with self._http_client.stream("GET", url, follow_redirects=False) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > MAX_ARTIFACT_BYTES:
                    raise _ArtifactTooLarge(f"larger than {MAX_ARTIFACT_BYTES} bytes")
                chunks.append(chunk)
        return b"".join(chunks)


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "DeliverPixelAgentsAssetsInput",
    "DeliverPixelAgentsAssetsOutput",
    "DeliverPixelAgentsAssetsTool",
]
