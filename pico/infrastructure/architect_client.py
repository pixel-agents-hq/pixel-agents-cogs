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

`ClientConfig(streaming=True, ...)`: `ask()` no longer only waits for one
aggregated final response -- when the consulted agent (architect) has its
own debug-logging setting on, it emits intermediate
`TaskStatusUpdateEvent`s (state `TASK_STATE_WORKING`, carrying a message)
mid-task, via the same a2a-sdk `TaskUpdater.update_status` its final
`.complete()` uses. Verified against the installed a2a-sdk
(`a2a/client/base_client.py: BaseClient.send_message`): the client only
receives those intermediate events, rather than a single collapsed final
response, when *both* this `streaming=True` *and* the resolved agent
card's own `capabilities.streaming` are true -- see
`architect/infrastructure/a2a_server.py`'s `build_agent_card()`. A
`TASK_STATE_WORKING` message is never the final answer (only
`TASK_STATE_COMPLETED`/`TASK_STATE_FAILED` are terminal), so `ask()`
distinguishes them by `status.state`, not just "has a message".
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class AgentAskResult:
    """One consulted agent's answer, plus whatever optional operational
    metadata it chose to report on its final message -- `tool_calls_made`,
    `successful_tool_calls`, `failed_tool_calls` are each None whenever
    their key is absent, since `ask()`'s own contract is generic across any
    future agent (see `ArchitectAsker`'s docstring in
    consult_agent_tool.py), not every one of which necessarily runs a
    bounded tool-calling loop or reports one."""

    answer: str
    tool_calls_made: int | None = None
    successful_tool_calls: int | None = None
    failed_tool_calls: int | None = None


class ArchitectClient:
    async def ask(
        self,
        *,
        base_url: str,
        text: str,
        on_activity: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentAskResult:
        """`on_activity`, if given, is awaited once per intermediate debug
        event the consulted agent chooses to emit mid-task (see this
        module's own docstring) -- optional and never invoked at all for an
        agent (or an agent with its own debug setting off) that only ever
        sends the one final response, so existing callers are unaffected."""

        httpx_client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        try:
            client = await create_client(
                base_url,
                client_config=ClientConfig(streaming=True, httpx_client=httpx_client),
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
            tool_calls_made: int | None = None
            successful_tool_calls: int | None = None
            failed_tool_calls: int | None = None
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
                        tool_calls_made = _metadata_int(
                            response.message.metadata, "tool_calls_made"
                        )
                        successful_tool_calls = _metadata_int(
                            response.message.metadata, "successful_tool_calls"
                        )
                        failed_tool_calls = _metadata_int(
                            response.message.metadata, "failed_tool_calls"
                        )
                    continue

                if status is not None and status.HasField("message"):
                    parts = [part.text for part in status.message.parts if part.text]
                    if parts:
                        joined = "\n".join(parts)
                        # TASK_STATE_WORKING is never the final answer -- an
                        # intermediate debug event architect chose to emit
                        # (see this module's own docstring). Only a terminal
                        # state's message is the real final_text.
                        if status.state == TaskState.TASK_STATE_WORKING:
                            if on_activity is not None:
                                await on_activity(joined)
                        else:
                            final_text = joined
                            tool_calls_made = _metadata_int(
                                status.message.metadata, "tool_calls_made"
                            )
                            successful_tool_calls = _metadata_int(
                                status.message.metadata, "successful_tool_calls"
                            )
                            failed_tool_calls = _metadata_int(
                                status.message.metadata, "failed_tool_calls"
                            )
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
        return AgentAskResult(
            answer=final_text,
            tool_calls_made=tool_calls_made,
            successful_tool_calls=successful_tool_calls,
            failed_tool_calls=failed_tool_calls,
        )


def _metadata_int(metadata: object, key: str) -> int | None:
    """`metadata` is a protobuf Struct -- membership/indexing work, but not
    `.get()` -- reporting any given key is a consulted agent's own choice
    (see `AgentAskResult`'s docstring), so a missing or non-numeric key is a
    normal case, not an error."""

    try:
        raw = metadata[key]  # type: ignore[index]
    except (KeyError, TypeError):
        return None
    return int(raw) if isinstance(raw, (int, float)) else None


__all__ = ["AgentAskResult", "ArchitectClient", "ArchitectRequestError"]
