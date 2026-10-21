"""handoff_claim：原子认领，只有一个赢家。"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class HandoffClaimTest(StewardTestCase):
    def test_claim_moves_pending_to_claimed(self):
        handoff = self.create_handoff()
        result = mcp_server.tool_handoff_claim(
            {"handoff_id": handoff["handoff"]["id"], "worker_session_id": "worker-a"}
        )
        self.assertTrue(result["claimed"])
        self.assertEqual(result["status"], "claimed")
        self.assertEqual(result["worker_session_id"], "worker-a")
        self.assertEqual(result["attempt"], 1)
        self.assertIsNotNone(result["handoff"]["claimed_at"])

    def test_another_worker_claiming_later_gets_claimed_false(self):
        handoff = self.create_handoff()
        handoff_id = handoff["handoff"]["id"]
        mcp_server.tool_handoff_claim({"handoff_id": handoff_id, "worker_session_id": "worker-a"})
        late = mcp_server.tool_handoff_claim(
            {"handoff_id": handoff_id, "worker_session_id": "worker-b"}
        )
        self.assertFalse(late["claimed"])
        self.assertEqual(late["status"], "claimed")
        self.assertEqual(late["worker_session_id"], "worker-a")
        self.assertEqual(late["handoff"]["worker_session_id"], "worker-a")

    def test_same_worker_claiming_again_promotes_to_running(self):
        handoff = self.create_handoff()
        handoff_id = handoff["handoff"]["id"]
        first = mcp_server.tool_handoff_claim(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a"}
        )
        self.assertEqual(first["status"], "claimed")
        second = mcp_server.tool_handoff_claim(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a"}
        )
        self.assertTrue(second["claimed"])
        self.assertEqual(second["status"], "running")
        # 自己再点一次不算重新认领，attempt 不涨。
        self.assertEqual(second["attempt"], 1)

    def test_two_workers_racing_leave_exactly_one_winner(self):
        handoff = self.create_handoff()
        handoff_id = handoff["handoff"]["id"]
        workers = ["worker-a", "worker-b"]
        barrier = threading.Barrier(len(workers))
        results = {}
        errors = {}

        def claim(worker):
            try:
                barrier.wait(timeout=10)
                results[worker] = mcp_server.tool_handoff_claim(
                    {"handoff_id": handoff_id, "worker_session_id": worker}
                )
            except Exception as cause:  # noqa: BLE001 - 测试要把异常记下来
                errors[worker] = cause

        threads = [threading.Thread(target=claim, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, {})
        winners = [worker for worker, result in results.items() if result["claimed"]]
        losers = [worker for worker, result in results.items() if not result["claimed"]]
        self.assertEqual(len(winners), 1, f"expected exactly one winner, got {results}")
        self.assertEqual(len(losers), 1)

        row = self.fetch_handoff_row(handoff_id)
        self.assertEqual(row["status"], "claimed")
        self.assertEqual(row["worker_session_id"], winners[0])
        self.assertEqual(row["attempt"], 1)

    def test_claim_of_a_missing_handoff_raises(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_claim({"handoff_id": 4242, "worker_session_id": "worker-a"})

    def test_claim_requires_a_worker_session_id(self):
        handoff = self.create_handoff()
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_claim({"handoff_id": handoff["handoff"]["id"], "worker_session_id": ""})

    def test_claim_of_a_finished_handoff_is_refused(self):
        handoff = self.create_handoff()
        handoff_id = handoff["handoff"]["id"]
        mcp_server.tool_handoff_claim({"handoff_id": handoff_id, "worker_session_id": "worker-a"})
        mcp_server.tool_handoff_finish(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a", "status": "done"}
        )
        after = mcp_server.tool_handoff_claim(
            {"handoff_id": handoff_id, "worker_session_id": "worker-b"}
        )
        self.assertFalse(after["claimed"])
        self.assertEqual(after["status"], "done")
