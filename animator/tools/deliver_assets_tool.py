"""Turns a finished pixel-art-mcp render job into real Discord attachments.

This is animator's only native tool -- every other capability (creating a
project, running Blender Python, rendering previews/sprites, polling a job)
comes for free through `AgentToolServerTool`, bridging whatever
`pixel-art-mcp` tools `[p]telephonepole` has registered and enabled for
`animator` (see `adapters/cog_base.py`'s `_mcp_tools`). This tool exists to
fix one specific gap: `pixel-art-mcp`'s own tools only ever return a
`download_url` as inert JSON text -- nothing turns that into an actual
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
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from corridor.domain import RegisteredTool

from .attachment import Attachment

log = logging.getLogger("red.animator")

_WANTED_FILENAMES = ("pixel-agents.zip", "preview.png")


class DeliverPixelAgentsAssetsInput(BaseModel):
    job_id: UUID = Field(
        description=(
            "The job ID of a render_sprites call whose status (via get_job) is already "
            "'succeeded', and which was called with pixel_agents set."
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
        "Call this once render_sprites has succeeded (poll get_job until status is "
        "'succeeded') for a job that was run with pixel_agents set. Fetches the "
        "resulting pixel-agents.zip and preview.png and attaches them to the reply "
        "sent back to Discord. Use this as your final step instead of describing the "
        "files in text -- a bare download_url is not reachable by a Discord user."
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
                    "Keep polling get_job until it reports 'succeeded', then call this again."
                ),
            )

        raw_artifacts = job.get("artifacts") or []
        artifacts: Sequence[Mapping[str, Any]] = (
            raw_artifacts if isinstance(raw_artifacts, Sequence) else []
        )
        attachments = await self._fetch_wanted_artifacts(artifacts)
        if not attachments:
            found = ", ".join(sorted({str(a.get("filename", "?")) for a in artifacts})) or "none"
            return DeliverPixelAgentsAssetsOutput(
                status="error",
                message=(
                    f"Job {raw_input.job_id} succeeded, but neither pixel-agents.zip nor "
                    f"preview.png was among its artifacts (found: {found})."
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
        self, artifacts: Sequence[Mapping[str, Any]]
    ) -> list[Attachment]:
        attachments: list[Attachment] = []
        for artifact in artifacts:
            filename = artifact.get("filename")
            download_url = artifact.get("download_url")
            if filename not in _WANTED_FILENAMES or not download_url:
                continue
            try:
                response = await self._http_client.get(download_url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("animator: could not download artifact %s: %s", filename, exc)
                continue
            media_type = artifact.get("media_type") or "application/octet-stream"
            attachments.append(
                Attachment(filename=filename, media_type=media_type, data=response.content)
            )
        return attachments


__all__ = [
    "DeliverPixelAgentsAssetsInput",
    "DeliverPixelAgentsAssetsOutput",
    "DeliverPixelAgentsAssetsTool",
]
