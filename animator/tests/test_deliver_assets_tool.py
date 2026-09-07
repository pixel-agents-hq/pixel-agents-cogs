"""DeliverPixelAgentsAssetsTool: turns a finished pixel-art-mcp render job
into staged `Attachment`s. Uses a fake `get_job` `RegisteredTool` (the same
shape `AgentToolServerTool` wraps) and a real `httpx.AsyncClient` bound to
an `httpx.MockTransport`, rather than mocking `httpx` itself -- exercises
the tool's actual GET calls end to end against scripted responses."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from uuid import uuid4

import httpx

from corridor.domain import RegisteredTool

from ..tools.deliver_assets_tool import DeliverPixelAgentsAssetsInput, DeliverPixelAgentsAssetsTool

_JOB_ID = uuid4()


def _get_job_tool(
    job_response: Mapping[str, object], *, calls: list[Mapping[str, object]] | None = None
) -> RegisteredTool:
    async def handler(_ctx: object, arguments: Mapping[str, object]) -> Mapping[str, object]:
        if calls is not None:
            calls.append(arguments)
        return job_response

    return RegisteredTool(
        name="get_job", description="Poll a job.", parameters={"type": "object"}, handler=handler
    )


def _succeeded_job(*, artifacts: list[dict[str, object]]) -> dict[str, object]:
    return {"id": str(_JOB_ID), "status": "succeeded", "artifacts": artifacts}


def _artifact(filename: str, media_type: str, url: str) -> dict[str, object]:
    return {"filename": filename, "media_type": media_type, "download_url": url}


def _http_client(responses: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return responses[str(request.url)]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _tool(
    *, registered_tools: tuple[RegisteredTool, ...], http_client: httpx.AsyncClient
) -> DeliverPixelAgentsAssetsTool:
    async def get_registered_tools() -> tuple[RegisteredTool, ...]:
        return registered_tools

    return DeliverPixelAgentsAssetsTool(
        get_registered_tools=get_registered_tools, http_client=http_client
    )


class TestDeliverPixelAgentsAssetsTool(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_both_expected_artifacts(self) -> None:
        job = _succeeded_job(
            artifacts=[
                _artifact("pixel-agents.zip", "application/zip", "http://x/artifacts/1"),
                _artifact("preview.gif", "image/gif", "http://x/artifacts/2"),
                _artifact("sprites.zip", "application/zip", "http://x/artifacts/3"),
            ]
        )
        http_client = _http_client(
            {
                "http://x/artifacts/1": httpx.Response(200, content=b"ZIPBYTES"),
                "http://x/artifacts/2": httpx.Response(200, content=b"GIFBYTES"),
            }
        )
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=http_client)

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "ok")
        filenames = {a.filename for a in output.attachments}
        self.assertEqual(filenames, {"pixel-agents.zip", "preview.gif"})
        zip_attachment = next(a for a in output.attachments if a.filename == "pixel-agents.zip")
        self.assertEqual(zip_attachment.data, b"ZIPBYTES")
        self.assertEqual(zip_attachment.media_type, "application/zip")

    async def test_falls_back_to_static_preview_png_when_no_gif_was_exported(self) -> None:
        job = _succeeded_job(
            artifacts=[
                _artifact("pixel-agents.zip", "application/zip", "http://x/artifacts/1"),
                _artifact("preview.png", "image/png", "http://x/artifacts/2"),
            ]
        )
        http_client = _http_client(
            {
                "http://x/artifacts/1": httpx.Response(200, content=b"ZIPBYTES"),
                "http://x/artifacts/2": httpx.Response(200, content=b"PNGBYTES"),
            }
        )
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=http_client)

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "ok")
        filenames = {a.filename for a in output.attachments}
        self.assertEqual(filenames, {"pixel-agents.zip", "preview.png"})

    async def test_prefers_animated_gif_over_static_png_when_both_are_present(self) -> None:
        job = _succeeded_job(
            artifacts=[
                _artifact("preview.png", "image/png", "http://x/artifacts/1"),
                _artifact("preview.gif", "image/gif", "http://x/artifacts/2"),
            ]
        )
        http_client = _http_client({"http://x/artifacts/2": httpx.Response(200, content=b"GIF")})
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=http_client)

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "ok")
        self.assertEqual([a.filename for a in output.attachments], ["preview.gif"])

    async def test_attachments_are_excluded_from_the_serialized_output(self) -> None:
        job = _succeeded_job(
            artifacts=[_artifact("preview.gif", "image/gif", "http://x/artifacts/2")]
        )
        http_client = _http_client({"http://x/artifacts/2": httpx.Response(200, content=b"GIF")})
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=http_client)

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertNotIn("attachments", output.model_dump_json())
        self.assertNotIn("GIF", output.model_dump_json())

    async def test_job_not_yet_succeeded_reports_an_error_without_attachments(self) -> None:
        job = {"id": str(_JOB_ID), "status": "running", "artifacts": []}
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=_http_client({}))

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "error")
        self.assertEqual(output.attachments, [])
        self.assertIn("running", output.message)

    async def test_succeeded_job_missing_wanted_artifacts_reports_an_error(self) -> None:
        job = _succeeded_job(
            artifacts=[_artifact("sprites.zip", "application/zip", "http://x/artifacts/3")]
        )
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=_http_client({}))

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "error")
        self.assertIn("sprites.zip", output.message)

    async def test_missing_get_job_tool_reports_a_clear_error(self) -> None:
        tool = _tool(registered_tools=(), http_client=_http_client({}))

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "error")
        self.assertIn("telephonepole", output.message)

    async def test_get_job_lookup_uses_the_string_job_id(self) -> None:
        job = _succeeded_job(artifacts=[])
        calls: list[Mapping[str, object]] = []
        get_job = _get_job_tool(job, calls=calls)
        tool = _tool(registered_tools=(get_job,), http_client=_http_client({}))

        await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(calls[0], {"job_id": str(_JOB_ID)})

    async def test_a_failed_artifact_download_is_skipped_not_raised(self) -> None:
        job = _succeeded_job(
            artifacts=[
                _artifact("pixel-agents.zip", "application/zip", "http://x/artifacts/1"),
                _artifact("preview.gif", "image/gif", "http://x/artifacts/2"),
            ]
        )
        http_client = _http_client(
            {
                "http://x/artifacts/1": httpx.Response(500),
                "http://x/artifacts/2": httpx.Response(200, content=b"GIF"),
            }
        )
        tool = _tool(registered_tools=(_get_job_tool(job),), http_client=http_client)

        output = await tool.handler(DeliverPixelAgentsAssetsInput(job_id=_JOB_ID))

        self.assertEqual(output.status, "ok")
        self.assertEqual([a.filename for a in output.attachments], ["preview.gif"])


if __name__ == "__main__":
    unittest.main()
