from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from damai_agent.providers import DemoProvider
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import JavaToolClient, ToolRegistry, build_java_tools


class StubJavaHandler(BaseHTTPRequestHandler):
    received: Dict[str, Any] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).received = {
            "path": self.path,
            "body": body,
            "api_key": self.headers.get("X-Agent-Key"),
            "turn_id": self.headers.get("X-Agent-Turn-Id"),
            "tool_call_id": self.headers.get("X-Agent-Tool-Call-Id"),
            "traceparent": self.headers.get("traceparent"),
        }
        response = {
            "requestId": self.headers.get("X-Agent-Tool-Call-Id"),
            "success": True,
            "code": 0,
            "message": "success",
            "data": {
                "pageNum": 1,
                "pageSize": 5,
                "totalSize": 1,
                "list": [
                    {
                        "id": 1001,
                        "title": "周杰伦嘉年华演唱会",
                        "place": "测试体育场",
                        "showTime": "2026-10-01 19:30:00",
                        "minPrice": 380,
                        "maxPrice": 1880,
                    }
                ],
            },
            "retryable": False,
            "freshnessAt": "2026-09-03T08:00:00Z",
        }
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


class JavaToolIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StubJavaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    async def test_demo_agent_calls_java_contract_with_tracing_headers(self) -> None:
        host, port = self.server.server_address
        client = JavaToolClient(
            base_url=f"http://{host}:{port}",
            api_key="integration-key",
            timeout_seconds=2,
        )
        runner = AgentRunner(
            provider=DemoProvider(),
            registry=ToolRegistry(build_java_tools(client)),
            sessions=InMemorySessionStore(),
        )

        result = await runner.run("帮我查询周杰伦的演唱会", "session-integration")

        self.assertIn("周杰伦嘉年华演唱会", result.answer)
        self.assertEqual(result.tool_calls, ["search_programs"])
        self.assertEqual(
            StubJavaHandler.received["path"],
            "/internal/agent/v1/tools/programs/search",
        )
        self.assertEqual(StubJavaHandler.received["api_key"], "integration-key")
        self.assertEqual(StubJavaHandler.received["body"]["keyword"], "周杰伦")
        self.assertTrue(StubJavaHandler.received["turn_id"].startswith("turn-"))
        self.assertTrue(StubJavaHandler.received["tool_call_id"].startswith("demo-"))
        traceparent = StubJavaHandler.received["traceparent"]
        self.assertRegex(traceparent, r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$")


if __name__ == "__main__":
    unittest.main()
