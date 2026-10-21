"""handoff_get / handoff_list：完整状态与有界摘要。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class HandoffGetListTest(StewardTestCase):
    def test_get_returns_the_full_handoff(self):
        created = self.create_handoff(
            attachments=[{"name": "take.wav", "path": "/tmp/take.wav"}],
            context={"projectId": "project_3", "bars": 8},
        )
        handoff_id = created["handoff"]["id"]
        fetched = mcp_server.tool_handoff_get({"handoff_id": handoff_id})
        self.assertEqual(fetched["handoff"], created["handoff"])

    def test_get_of_a_missing_handoff_raises(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_get({"handoff_id": 999})

    def test_get_rejects_a_bad_id(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_get({"handoff_id": "not-an-id"})

    def test_list_filters_by_status_and_target(self):
        studio = self.create_handoff(idempotency_key="s1", target="studio")
        dj = self.create_handoff(idempotency_key="d1", target="dj")
        mcp_server.tool_handoff_claim(
            {"handoff_id": dj["handoff"]["id"], "worker_session_id": "worker-a"}
        )

        pending = mcp_server.tool_handoff_list({"status": "pending"})
        self.assertEqual([item["id"] for item in pending["handoffs"]], [studio["handoff"]["id"]])
        self.assertEqual(pending["total"], 1)

        claimed = mcp_server.tool_handoff_list({"status": "claimed"})
        self.assertEqual([item["id"] for item in claimed["handoffs"]], [dj["handoff"]["id"]])

        by_target = mcp_server.tool_handoff_list({"target": "dj"})
        self.assertEqual([item["id"] for item in by_target["handoffs"]], [dj["handoff"]["id"]])
        self.assertEqual(by_target["total"], 1)

    def test_list_rejects_unknown_filters(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_list({"status": "nonsense"})
        with self.assertRaises(ValueError):
            mcp_server.tool_handoff_list({"target": "producer-tools"})

    def test_list_is_bounded(self):
        for index in range(8):
            self.create_handoff(idempotency_key=f"k{index}")
        listed = mcp_server.tool_handoff_list({"limit": 3})
        self.assertEqual(len(listed["handoffs"]), 3)
        self.assertEqual(listed["count"], 3)
        self.assertEqual(listed["total"], 8)

        capped = mcp_server.tool_handoff_list({"limit": 9999})
        self.assertLessEqual(len(capped["handoffs"]), mcp_server.MAX_LIST_LIMIT)

    def test_list_summary_has_no_attachment_paths(self):
        self.create_handoff(attachments=[{"name": "secret.wav", "path": "/tmp/secret.wav"}])
        listed = mcp_server.tool_handoff_list({})
        self.assertEqual(listed["handoffs"][0]["attachment_count"], 1)
        self.assertNotIn("/tmp/secret.wav", str(listed))
