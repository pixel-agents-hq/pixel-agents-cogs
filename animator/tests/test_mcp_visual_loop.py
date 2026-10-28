"""MCP adapter -> animator -> multimodal LLM wire, without a real model or Discord."""

import json
import unittest

from corridor.domain import RegisteredTool
from corridor.infrastructure.llm_client import ChatCompletionRequest, ImageContentPart
from corridor.infrastructure.mcp_client import McpImage, McpResource, McpToolResult

from ..application.tool_loop_service import ToolLoopService
from ..tools.agent_tool_server import AgentToolServerTool
from .test_tool_loop_service import EchoTool, ScriptedLLM, _response, _tool_call


class TestMcpVisualLoop(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, llm, tools, **kwargs):
        return await ToolLoopService(llm).run(
            base_url="https://x",
            api_key="k",
            model="vision",
            system_prompt="Custom prompt",
            user_input="Create an asset",
            tools=tools,
            max_tool_calls=30,
            **kwargs,
        )

    def media_tool(self, name="get_asset_preview"):
        async def handler(_ctx, _args):
            result = McpToolResult({"native_size": [16, 32], "filename": "pixel-agents.zip"})
            result.images = (McpImage("image/png", "iVBORw=="),)
            result.resources = (McpResource("pixel-art://artifact/id", "application/zip", b"PK"),)
            return result

        return AgentToolServerTool(
            RegisteredTool(
                name=name,
                description="Preview",
                parameters={
                    "type": "object",
                    "properties": {},
                },
                handler=handler,
                server_instructions="Use drawing commands",
            )
        )

    async def test_preview_reaches_model_once_all_tool_results_are_paired(self):
        tool = self.media_tool()
        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[
                        _tool_call("image", name=tool.name, arguments="{}"),
                        _tool_call("echo"),
                    ]
                ),
                _response(content="Seen"),
            ]
        )
        debug = []

        async def log_event(text):
            debug.append(text)

        result = await self.run_loop(llm, [tool, EchoTool()], debug=True, on_debug_event=log_event)
        self.assertEqual(result.text, "Seen")
        messages = llm.calls[-1]["messages"]
        self.assertEqual(
            [m.role for m in messages],
            ["system", "system", "user", "assistant", "tool", "tool", "user"],
        )
        self.assertIn("Use drawing commands", messages[1].content)
        wire = ChatCompletionRequest(model="vision", messages=messages).model_dump(mode="json")
        self.assertEqual(
            wire["messages"][-1]["content"][1]["image_url"]["url"], "data:image/png;base64,iVBORw=="
        )
        self.assertNotIn("iVBORw==", "\n".join(debug))
        self.assertNotIn("iVBORw==", messages[4].content)
        self.assertEqual(result.attachments, ())

    async def test_resource_can_be_delivered_without_http_download(self):
        tool = self.media_tool("get_artifact")
        output = await tool.handler(tool.Input.model_validate({}))
        self.assertEqual(output.resources[0].data, b"PK")
        self.assertEqual(output.attachments[0].filename, "pixel-agents.zip")
        self.assertNotIn("PK", output.model_dump_json())
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("f", name=tool.name, arguments="{}")]),
                _response(content="Attached"),
            ]
        )
        result = await self.run_loop(llm, [tool])
        self.assertEqual(result.attachments[0].data, b"PK")

    async def test_image_history_is_bounded(self):
        tool = self.media_tool()
        llm = ScriptedLLM(
            [
                *[
                    _response(tool_calls=[_tool_call(str(i), name=tool.name, arguments="{}")])
                    for i in range(10)
                ],
                _response(content="Done"),
            ]
        )
        await self.run_loop(llm, [tool])
        messages = llm.calls[-1]["messages"]
        images = [
            p
            for m in messages
            if isinstance(m.content, list)
            for p in m.content
            if isinstance(p, ImageContentPart)
        ]
        self.assertEqual(len(images), 8)

    async def test_truncated_response_does_not_execute_even_valid_prefix_calls(self):
        tool = EchoTool()
        truncated = _response(
            tool_calls=[_tool_call("first"), _tool_call("broken", arguments='{"text":')]
        )
        truncated.choices[0].finish_reason = "length"
        llm = ScriptedLLM(
            [truncated, _response(tool_calls=[_tool_call("retry")]), _response(content="Done")]
        )
        result = await self.run_loop(llm, [tool])
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(result.failed_tool_calls, 2)
        self.assertIn("No calls", llm.calls[-1]["messages"][3].content)
        for message in llm.calls[-1]["messages"]:
            for call in message.tool_calls or []:
                self.assertIsInstance(json.loads(call.function.arguments), dict)

    async def test_truncated_text_is_not_reported_as_success(self):
        response = _response(content="Unfinished")
        response.choices[0].finish_reason = "length"
        result = await self.run_loop(ScriptedLLM([response]), [])
        self.assertEqual(result.stopped_reason, "llm_error")

    async def test_malformed_json_can_be_retried_without_poisoning_history(self):
        tool = EchoTool()
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("bad", arguments='{"text":')]),
                _response(tool_calls=[_tool_call("retry")]),
                _response(content="Done"),
            ]
        )
        result = await self.run_loop(llm, [tool])
        self.assertEqual((result.successful_tool_calls, result.failed_tool_calls), (1, 1))
        self.assertEqual(llm.calls[-1]["messages"][2].tool_calls[0].function.arguments, "{}")

    async def test_identical_server_instructions_are_not_repeated_per_tool(self):
        llm = ScriptedLLM([_response(content="Done")])
        await self.run_loop(llm, [self.media_tool(), self.media_tool("get_artifact")])
        self.assertEqual([m.role for m in llm.calls[0]["messages"]], ["system", "system", "user"])
