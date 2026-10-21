"""handoff_finish：只有认领者能写，终态幂等，非法回退拒绝。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class HandoffFinishTest(StewardTestCase):
    def claimed_handoff(self, worker="worker-a"):
        handoff = self.create_handoff()
        handoff_id = handoff["handoff"]["id"]
        mcp_server.tool_handoff_claim({"handoff_id": handoff_id, "worker_session_id": worker})
        return handoff_id

    def test_finish_done_records_the_outcome(self):
        handoff_id = self.claimed_handoff()
        result = mcp_server.tool_handoff_finish(
            {
                "handoff_id": handoff_id,
                "worker_session_id": "worker-a",
                "status": "done",
                "outcome": "8 小节 demo 已渲染",
                "metrics": {"bars": 8, "render_ms": 1200},
            }
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["status"], "done")
        handoff = result["handoff"]
        self.assertEqual(handoff["status"], "done")
        self.assertEqual(handoff["outcome"], "8 小节 demo 已渲染")
        self.assertEqual(handoff["metrics"], {"bars": 8, "render_ms": 1200})
        self.assertIsNotNone(handoff["finished_at"])

    def test_finish_is_idempotent_for_the_same_terminal_status(self):
        handoff_id = self.claimed_handoff()
        first = mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "done"}
        )
        second = mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "done"}
        )
        self.assertTrue(second["ok"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["handoff"]["finished_at"], first["handoff"]["finished_at"])

    def test_finish_refuses_to_rewrite_a_terminal_status(self):
        handoff_id = self.claimed_handoff()
        mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "done"}
        )
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_finish(
                {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "failed"}
            )
        row = self.fetch_handoff_row(handoff_id)
        self.assertEqual(row["status"], "done")

    def test_finish_rejects_a_worker_that_did_not_claim(self):
        handoff_id = self.claimed_handoff(worker="worker-a")
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_finish(
                {"handoff_id": handoff_id, "worker_session_id": "worker-b", "status": "done"}
            )
        row = self.fetch_handoff_row(handoff_id)
        self.assertEqual(row["status"], "claimed")
        self.assertIsNone(row["finished_at"])

    def test_finish_rejects_an_unclaimed_handoff(self):
        handoff = self.create_handoff()
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_finish(
                {
                    "handoff_id": handoff["handoff"]["id"],
                    "worker_session_id": "worker-a",
                    "status": "done",
                }
            )
        row = self.fetch_handoff_row(handoff["handoff"]["id"])
        self.assertEqual(row["status"], "pending")

    def test_failed_keeps_the_error_text(self):
        handoff_id = self.claimed_handoff()
        result = mcp_server.tool_handoff_finish(
            {
                "handoff_id": handoff_id,
                "worker_session_id": "worker-a",
                "status": "failed",
                "error": "voice_to_drums exited 1",
            }
        )
        self.assertEqual(result["handoff"]["status"], "failed")
        self.assertEqual(result["handoff"]["error"], "voice_to_drums exited 1")

    def test_cancelled_is_a_terminal_state_too(self):
        handoff_id = self.claimed_handoff()
        result = mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "cancelled"}
        )
        self.assertEqual(result["handoff"]["status"], "cancelled")
        again = mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "cancelled"}
        )
        self.assertTrue(again["idempotent"])

    def test_finish_rejects_a_non_terminal_status(self):
        handoff_id = self.claimed_handoff()
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_finish(
                {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "running"}
            )

    def test_finish_rejects_oversized_metrics(self):
        handoff_id = self.claimed_handoff()
        metrics = {f"k{index}": index for index in range(mcp_server.MAX_METRICS_KEYS + 1)}
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_finish(
                {
                    "handoff_id": handoff_id,
                    "worker_session_id": "worker-a",
                    "status": "done",
                    "metrics": metrics,
                }
            )
