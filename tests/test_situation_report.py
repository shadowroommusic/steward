"""situation_report：老键一个不少，新的 handoff 段落有界。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class SituationReportTest(StewardTestCase):
    def test_keeps_the_existing_keys(self):
        mcp_server.tool_report_done({"actor": "studio:project_3", "type": "render.done"})
        report = mcp_server.tool_situation_report({})
        for key in ("summary", "actors", "errors", "events", "last_injected_event_id"):
            self.assertIn(key, report)
        self.assertEqual(report["actors"], ["studio:project_3"])
        self.assertEqual(len(report["events"]), 1)

    def test_reports_the_handoff_section(self):
        self.create_handoff(idempotency_key="k1")
        self.create_handoff(idempotency_key="k2", target="dj")
        report = mcp_server.tool_situation_report({})
        handoffs = report["handoffs"]
        self.assertEqual(handoffs["total"], 2)
        self.assertEqual(handoffs["counts"]["pending"], 2)
        self.assertEqual(len(handoffs["active"]), 2)
        self.assertEqual(handoffs["recent"], [])

    def test_handoff_section_is_bounded(self):
        long_instruction = "很长的指令" * 400
        for index in range(9):
            self.create_handoff(
                idempotency_key=f"k{index}",
                instruction=long_instruction,
                attachments=[{"name": "a.wav", "path": f"/tmp/a{index}.wav"}],
            )
        report = mcp_server.tool_situation_report({})
        handoffs = report["handoffs"]
        self.assertEqual(handoffs["total"], 9)
        self.assertLessEqual(len(handoffs["active"]), mcp_server.ACTIVE_SUMMARY_LIMIT)
        self.assertLessEqual(len(handoffs["recent"]), mcp_server.RECENT_SUMMARY_LIMIT)
        for item in handoffs["active"] + handoffs["recent"]:
            self.assertLessEqual(len(item["instruction_preview"]), mcp_server.MAX_SUMMARY_CHARS + 1)
            self.assertNotIn("instruction", item)
            self.assertNotIn("attachments", item)

    def test_report_never_leaks_attachment_paths(self):
        self.create_handoff(attachments=[{"name": "voice.m4a", "path": "/tmp/voice-1234.m4a"}])
        report = mcp_server.tool_situation_report({})
        self.assertNotIn("/tmp/voice-1234.m4a", json.dumps(report, ensure_ascii=False))

    def test_active_and_recent_are_split_by_status(self):
        done = self.create_handoff(idempotency_key="done-1")
        handoff_id = done["handoff"]["id"]
        mcp_server.tool_handoff_claim({"handoff_id": handoff_id, "worker_session_id": "worker-a"})
        mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "done"}
        )
        self.create_handoff(idempotency_key="pending-1")

        report = mcp_server.tool_situation_report({})
        handoffs = report["handoffs"]
        self.assertEqual([item["id"] for item in handoffs["active"]], [2])
        self.assertEqual([item["id"] for item in handoffs["recent"]], [handoff_id])
        self.assertEqual(handoffs["counts"]["done"], 1)
        self.assertEqual(handoffs["counts"]["pending"], 1)

    def test_summary_mentions_pending_handoffs(self):
        self.create_handoff()
        report = mcp_server.tool_situation_report({})
        self.assertIn("交接待办", report["summary"])
