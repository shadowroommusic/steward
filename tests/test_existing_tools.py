"""老工具不回退，app_command 成为管家唯一的命令入口。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class ExistingToolsTest(StewardTestCase):
    def test_report_done_writes_one_event(self):
        result = mcp_server.tool_report_done(
            {
                "actor": "studio:project_3",
                "type": "render.done",
                "object_id": "project_3",
                "outcome": "ok",
                "file": "exports/demo.wav",
                "metrics": {"bars": 8, "ms": 1200},
            }
        )
        self.assertIn("event_id", result)
        self.assertEqual(self.count_rows("events"), 1)

    def test_report_done_keeps_at_most_three_metrics(self):
        mcp_server.tool_report_done(
            {"actor": "studio", "metrics": {"a": 1, "b": 2, "c": 3, "d": 4}}
        )
        events = mcp_server.tool_recent_events({})
        self.assertEqual(len(events["events"][0]["payload"]["metrics"]), 3)

    def test_recent_events_respects_since_id_and_limit(self):
        first = mcp_server.tool_report_done({"actor": "studio"})
        second = mcp_server.tool_report_done({"actor": "dj"})
        events = mcp_server.tool_recent_events({"limit": 1})
        self.assertEqual([event["id"] for event in events["events"]], [second["event_id"]])
        since = mcp_server.tool_recent_events({"since_id": first["event_id"]})
        self.assertEqual([event["id"] for event in since["events"]], [second["event_id"]])

    def test_focus_change_still_records_an_event(self):
        result = mcp_server.tool_focus_change(
            {"workspace": "studio", "object_id": "project_3", "object_type": "project", "actor": "ui"}
        )
        self.assertIn("event_id", result)
        events = mcp_server.tool_recent_events({})
        self.assertEqual(events["events"][0]["type"], "focus.change")
        self.assertEqual(events["events"][0]["payload"]["metrics"], {"workspace": "studio"})

    def test_navigate_is_a_side_effect_free_typed_command(self):
        before_events = self.count_rows("events")
        before_handoffs = self.count_rows("handoffs")
        result = mcp_server.tool_app_command(
            {
                "op": "navigate",
                "view": "studio",
                "reason": "打开 Studio",
            }
        )
        self.assertEqual(result["op"], "navigate")
        self.assertEqual(result["view"], "studio")
        # 纯分类：一个字节都不许写。
        self.assertEqual(self.count_rows("events"), before_events)
        self.assertEqual(self.count_rows("handoffs"), before_handoffs)

    def test_dispatch_wraps_the_validated_domain_route(self):
        result = mcp_server.tool_app_command(
            {
                "op": "dispatch",
                "route": {
                    "target": "dj",
                    "action_summary": "接下一首",
                    "confidence": 0.8,
                    "context": {},
                },
            }
        )
        self.assertEqual(result["op"], "dispatch")
        self.assertEqual(result["route"]["target"], "dj")
        self.assertFalse(result["route"]["context_satisfied"])
        self.assertEqual(sorted(result["route"]["missing_context"]), ["dj", "setId"])

    def test_app_command_rejects_unknown_view_and_free_form_fields(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_app_command({"op": "navigate", "view": "producer-tools"})
        with self.assertRaises(ValueError):
            mcp_server.tool_app_command(
                {"op": "navigate", "view": "studio", "selector": ".sr-dock"}
            )

    def test_app_command_rejects_cross_wired_navigate_and_dispatch_shapes(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_app_command(
                {"op": "navigate", "view": "studio", "route": {"target": "studio"}}
            )
        with self.assertRaises(ValueError):
            mcp_server.tool_app_command(
                {
                    "op": "dispatch",
                    "view": "studio",
                    "route": {
                        "target": "studio",
                        "action_summary": "做一段鼓",
                        "confidence": 0.9,
                    },
                }
            )
