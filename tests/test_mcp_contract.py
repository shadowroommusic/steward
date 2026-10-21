"""MCP 契约：工具清单、调用往返、错误处理。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class McpContractTest(StewardTestCase):
    def test_tools_list_exposes_every_tool(self):
        result = mcp_server.handle({"method": "tools/list"})
        names = [tool["name"] for tool in result["tools"]]
        for name in (
            "report_done",
            "recent_events",
            "situation_report",
            "focus_change",
            "route_intent",
            "handoff_create",
            "handoff_claim",
            "handoff_finish",
            "handoff_get",
            "handoff_list",
        ):
            self.assertIn(name, names)
        for tool in result["tools"]:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_tools_call_round_trips_through_json(self):
        created = mcp_server.handle(
            {
                "method": "tools/call",
                "params": {
                    "name": "handoff_create",
                    "arguments": {
                        "target": "dj",
                        "instruction": "把这段接进 set 的下一首",
                        "idempotency_key": "bus-1",
                        "attachments": [{"name": "take.wav", "path": "/tmp/take.wav"}],
                        "context": {"setId": "set_2"},
                    },
                },
            }
        )
        payload = json.loads(created["content"][0]["text"])
        self.assertTrue(payload["created"])
        self.assertEqual(payload["handoff"]["target"], "dj")
        self.assertEqual(payload["handoff"]["context"], {"setId": "set_2"})

        fetched = mcp_server.handle(
            {
                "method": "tools/call",
                "params": {"name": "handoff_get", "arguments": {"handoff_id": payload["handoff"]["id"]}},
            }
        )
        self.assertEqual(
            json.loads(fetched["content"][0]["text"])["handoff"]["id"], payload["handoff"]["id"]
        )

    def test_tools_call_rejects_an_unknown_tool(self):
        with self.assertRaises(ValueError):
            mcp_server.handle({"method": "tools/call", "params": {"name": "nope", "arguments": {}}})

    def test_initialize_reports_the_server_version(self):
        result = mcp_server.handle({"method": "initialize"})
        self.assertEqual(result["serverInfo"]["name"], "steward")
        self.assertEqual(result["serverInfo"]["version"], "0.2.0")

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            mcp_server.handle({"method": "resources/list"})
