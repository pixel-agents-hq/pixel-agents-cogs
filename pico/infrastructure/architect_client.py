"""Thin A2A client wrapper for reaching architect. Uses the real a2a-sdk
client directly (protobuf wire types, same SDK generation
`corridor/infrastructure/a2a_server.py`'s module docstring describes) --
verified against a live loopback architect listener during development;
see docs/architect-design.md section 4's sequence diagram.

`create_client` resolves the agent card from architect's `base_url` on
every call rather than caching a client keyed to a possibly-stale URL --
the owner-configurable `[p]pico architect url` can change at runtime, and
this call is already off pico's message-gate fast path (it only runs
inside a tool call a triggering LLM turn chose to make).
"""

from __future__ import annotations

import logging
import uuid

import httpx
from a2a.client import ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

log = logging.getLogger("red.pico")

# a2a-sdk falls back to a plain `httpx.AsyncClient()` -- httpx's own
# default timeout (5.0s total, every phase) -- when ClientConfig isn't
# given one of its own. That's far too short here: unlike a typical HTTP
# call, one `ask()` waits on architect's *entire* bounded tool-calling
# loop, which can make several sequential corridor LLM round trips
# (up to architect's own `max_tool_calls`) before returning. A real
# production timeout (`consult_architect failed: Client Request timed
# out`, 5s after the request went out) is exactly this default in action,
# not a hang or a bug in architect's tool loop.
_REQUEST_TIMEOUT_SECONDS = 120.0


class ArchitectRequestError(RuntimeError):
    """Raised on any failure to get a completed answer from architect --
    unreachable listener, a task that ends in TASK_STATE_FAILED, or a
    response with no text content. Callers (ArchitectTool) catch this and
    report it back to the LLM as a tool error, the same way
    LLMRequestError is handled elsewhere in this cog."""


class ArchitectClient:
    async def ask(self, *, base_url: str, text: str) -> str:
        httpx_client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        try:
            client = await create_client(
                base_url,
                client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
            )
        except Exception as exc:
            await httpx_client.aclose()
            raise ArchitectRequestError(f"Could not reach architect at {base_url}: {exc}") from exc

        try:
            message = Message(
                message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)]
            )
            request = SendMessageRequest(message=message)
            final_text: str | None = None
            failed = False
            async for response in client.send_message(request):
                status = None
                if response.HasField("task"):
                    status = response.task.status
                elif response.HasField("status_update"):
                    status = response.status_update.status
                elif response.HasField("message"):
                    parts = [part.text for part in response.message.parts if part.text]
                    if parts:
                        final_text = "\n".join(parts)
                    continue

                if status is not None and status.HasField("message"):
                    parts = [part.text for part in status.message.parts if part.text]
                    if parts:
                        final_text = "\n".join(parts)
                if status is not None and status.state == TaskState.TASK_STATE_FAILED:
                    failed = True
                    break
                if status is not None and status.state == TaskState.TASK_STATE_COMPLETED:
                    break
        except Exception as exc:
            raise ArchitectRequestError(f"architect request failed: {exc}") from exc
        finally:
            await client.close()

        if failed or final_text is None:
            raise ArchitectRequestError(final_text or "architect did not return an answer")
        return final_text


__all__ = ["ArchitectClient", "ArchitectRequestError"]
