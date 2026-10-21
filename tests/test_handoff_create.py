"""handoff_create：幂等 + 边界。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class HandoffCreateTest(StewardTestCase):
    def test_create_returns_a_pending_handoff(self):
        result = self.create_handoff(
            attachments=[{"name": "voice.m4a", "path": "/tmp/voice.m4a"}],
            source_session_id="butler-1",
            context={"projectId": "project_3"},
        )
        handoff = result["handoff"]
        self.assertTrue(result["created"])
        self.assertFalse(result["duplicate"])
        self.assertEqual(handoff["status"], "pending")
        self.assertEqual(handoff["target"], "studio")
        self.assertEqual(handoff["attempt"], 0)
        self.assertIsNone(handoff["worker_session_id"])
        self.assertIsNone(handoff["claimed_at"])
        self.assertIsNone(handoff["finished_at"])
        self.assertEqual(handoff["attachments"], [{"name": "voice.m4a", "path": "/tmp/voice.m4a"}])
        self.assertEqual(handoff["context"], {"projectId": "project_3"})
        self.assertEqual(handoff["source_session_id"], "butler-1")
        self.assertGreater(handoff["created_at"], 0)

    def test_same_idempotency_key_returns_the_same_handoff(self):
        first = self.create_handoff()
        second = self.create_handoff()
        self.assertEqual(first["handoff"]["id"], second["handoff"]["id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["duplicate"])
        self.assertFalse(second["conflict"])
        self.assertEqual(self.count_rows("handoffs"), 1)

    def test_same_key_with_a_different_payload_is_reported_as_conflict(self):
        first = self.create_handoff()
        second = self.create_handoff(instruction="换个活")
        self.assertEqual(first["handoff"]["id"], second["handoff"]["id"])
        self.assertTrue(second["conflict"])
        # 已存在的那条**不被覆盖**。
        self.assertEqual(second["handoff"]["instruction"], "把这段旋律铺成 8 小节 demo")
        self.assertEqual(self.count_rows("handoffs"), 1)

    def test_different_keys_create_different_handoffs(self):
        first = self.create_handoff(idempotency_key="route-1")
        second = self.create_handoff(idempotency_key="route-2")
        self.assertNotEqual(first["handoff"]["id"], second["handoff"]["id"])
        self.assertEqual(self.count_rows("handoffs"), 2)

    def test_rejects_unknown_target(self):
        with self.assertRaises(ValueError):
            self.create_handoff(target="producer-tools")

    def test_rejects_empty_instruction_and_key(self):
        with self.assertRaises(ValueError):
            self.create_handoff(instruction="   ")
        with self.assertRaises(ValueError):
            self.create_handoff(idempotency_key="")

    def test_rejects_too_many_attachments(self):
        attachments = [
            {"name": f"a{index}.wav", "path": f"/tmp/a{index}.wav"}
            for index in range(mcp_server.MAX_ATTACHMENTS + 1)
        ]
        with self.assertRaises(ValueError):
            self.create_handoff(attachments=attachments)

    def test_rejects_overlong_fields(self):
        with self.assertRaises(ValueError):
            self.create_handoff(instruction="长" * (mcp_server.MAX_INSTRUCTION + 1))
        with self.assertRaises(ValueError):
            self.create_handoff(idempotency_key="k" * (mcp_server.MAX_IDEMPOTENCY_KEY + 1))
        with self.assertRaises(ValueError):
            self.create_handoff(
                attachments=[{"name": "n" * (mcp_server.MAX_ATTACHMENT_NAME + 1), "path": "/tmp/a"}]
            )
        with self.assertRaises(ValueError):
            self.create_handoff(
                attachments=[{"name": "a", "path": "/" + "p" * mcp_server.MAX_ATTACHMENT_PATH}]
            )

    def test_rejects_oversized_context(self):
        with self.assertRaises(ValueError):
            self.create_handoff(context={"blob": "x" * (mcp_server.MAX_CONTEXT_BYTES + 1)})

    def test_rejects_malformed_attachments_and_context(self):
        with self.assertRaises(ValueError):
            self.create_handoff(attachments={"name": "a"})
        with self.assertRaises(ValueError):
            self.create_handoff(attachments=[{"name": "a"}])
        with self.assertRaises(ValueError):
            self.create_handoff(attachments=["/tmp/a.wav"])
        with self.assertRaises(ValueError):
            self.create_handoff(context=["not", "an", "object"])
