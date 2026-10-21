"""老工具不回退：report_done / recent_events / focus_change / route_intent。"""

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

    def test_route_intent_classifies_without_touching_the_ledger(self):
        before_events = self.count_rows("events")
        before_handoffs = self.count_rows("handoffs")
        result = mcp_server.tool_route_intent(
            {
                "target": "studio",
                "action_summary": "把这段旋律铺成 demo",
                "confidence": 0.92,
                "required_context": ["projectId"],
                "context": {"workspace": "studio", "projectId": "project_3"},
            }
        )
        self.assertEqual(result["target"], "studio")
        self.assertTrue(result["context_satisfied"])
        self.assertEqual(result["missing_context"], [])
        self.assertEqual(result["confidence"], 0.92)
        # 纯分类：一个字节都不许写。
        self.assertEqual(self.count_rows("events"), before_events)
        self.assertEqual(self.count_rows("handoffs"), before_handoffs)

    def test_route_intent_reports_missing_context(self):
        result = mcp_server.tool_route_intent(
            {"target": "dj", "action_summary": "接下一首", "confidence": 0.8, "context": {}}
        )
        self.assertFalse(result["context_satisfied"])
        self.assertEqual(sorted(result["missing_context"]), ["dj", "setId"])

    def test_route_intent_rejects_an_unknown_target(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_route_intent(
                {"target": "producer-tools", "action_summary": "x", "confidence": 0.5}
            )
