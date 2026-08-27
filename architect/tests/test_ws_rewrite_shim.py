"""Executes the actual injected `<script>` shims in real Node.js, not a
transcription of what they're supposed to do -- verifies the exact browser
behavior this feature depends on: the bundle's hardcoded, page-path-
independent `wss://<host>/ws` connection (see docs/architect-design.md's
incident note) must be rewritten to architect's own `WEBSOCKET_PATH`
before a real `WebSocket` is ever constructed, and TICKET_SHIM's own
`/ws`-detection (layered on top, injected second) must still fire against
the original, unrewritten URL.

Skipped if `node` isn't on PATH -- this is an extra verification layer for
browser-side JavaScript this suite otherwise can't exercise at all, not a
required part of the Python test suite's own coverage.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest

from ..infrastructure.webview import TICKET_SHIM, WEBSOCKET_PATH, WS_REWRITE_SHIM

_NODE = shutil.which("node")


def _script_body(shim: str) -> str:
    match = re.search(r"<script>(.*)</script>", shim, re.DOTALL)
    assert match is not None, "shim is not a single <script>...</script> block"
    return match.group(1)


def _run_node(js: str) -> dict[str, object]:
    """Runs `js` in Node with a minimal fake `window`/`WebSocket`/`fetch`
    browser harness, then prints one JSON object describing what the
    final `window.WebSocket` constructor actually did -- returned parsed."""

    harness = f"""
    var capturedUrls = [];
    var sentPayloads = [];
    function FakeWebSocket(url, protocols) {{
      capturedUrls.push(url);
      this.url = url;
      this.readyState = 0;
      this.OPEN = 1;
    }}
    FakeWebSocket.CONNECTING = 0;
    FakeWebSocket.OPEN = 1;
    FakeWebSocket.CLOSING = 2;
    FakeWebSocket.CLOSED = 3;
    FakeWebSocket.prototype.send = function (payload) {{ sentPayloads.push(payload); }};
    FakeWebSocket.prototype.addEventListener = function () {{}};
    FakeWebSocket.prototype.removeEventListener = function () {{}};
    global.location = {{ pathname: '/x' }};
    global.window = {{ WebSocket: FakeWebSocket, location: global.location }};
    global.fetch = function () {{ return Promise.reject(new Error('no network in test')); }};

    {js}

    console.log(JSON.stringify({{
      capturedUrls: capturedUrls,
      sentPayloads: sentPayloads,
      finalConstructorIsPatched: window.WebSocket !== FakeWebSocket,
    }}));
    """
    result = subprocess.run(
        [_NODE, "-e", harness], capture_output=True, text=True, timeout=10, check=True
    )
    return json.loads(result.stdout)


@unittest.skipUnless(_NODE, "node is not installed")
class TestWsRewriteShim(unittest.TestCase):
    def test_rewrites_a_bare_ws_path_to_websocket_path(self) -> None:
        js = _script_body(WS_REWRITE_SHIM) + "\nnew window.WebSocket('wss://example.test/ws');"

        result = _run_node(js)

        self.assertEqual(result["capturedUrls"], [f"wss://example.test{WEBSOCKET_PATH}"])

    def test_preserves_a_query_string_after_ws(self) -> None:
        js = (
            _script_body(WS_REWRITE_SHIM)
            + "\nnew window.WebSocket('wss://example.test/ws?token=abc');"
        )

        result = _run_node(js)

        self.assertEqual(result["capturedUrls"], [f"wss://example.test{WEBSOCKET_PATH}?token=abc"])

    def test_leaves_an_unrelated_path_containing_ws_untouched(self) -> None:
        js = (
            _script_body(WS_REWRITE_SHIM)
            + "\nnew window.WebSocket('wss://example.test/wsx/other');"
        )

        result = _run_node(js)

        self.assertEqual(result["capturedUrls"], ["wss://example.test/wsx/other"])

    def test_composes_with_ticket_shim_rewriting_the_real_connection(self) -> None:
        """TICKET_SHIM injected second (matching dashboard_webview_response's
        real injection order) must still see the *original* URL for its own
        `/ws` detection, while the actual WebSocket it constructs goes to
        the rewritten target -- both shims' jobs done correctly at once."""

        js = (
            _script_body(WS_REWRITE_SHIM)
            + "\n"
            + _script_body(TICKET_SHIM)
            + "\nnew window.WebSocket('wss://example.test/ws');"
        )

        result = _run_node(js)

        self.assertEqual(result["capturedUrls"], [f"wss://example.test{WEBSOCKET_PATH}"])
        self.assertTrue(result["finalConstructorIsPatched"])
