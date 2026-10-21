"""JSON 往返：一条交接进库再出来，一个字节都不许变。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class JsonRoundTripTest(StewardTestCase):
    def test_handoff_survives_a_json_round_trip(self):
        created = self.create_handoff(
            attachments=[
                {"name": "voice.m4a", "path": "/tmp/voice.m4a"},
                {"name": "midi", "path": "/tmp/roll.mid"},
            ],
            source_session_id="butler-7",
            context={"projectId": "project_3", "bars": 8, "nested": {"zh": "中文"}},
        )
        handoff_id = created["handoff"]["id"]
        mcp_server.tool_handoff_claim(
            {"handoff_id": handoff_id, "worker_session_id": "worker-a"}
        )
        mcp_server.tool_handoff_finish(
            {
                "handoff_id": handoff_id,
                "worker_session_id": "worker-a",
                "status": "done",
                "outcome": "done",
                "metrics": {"bars": 8},
            }
        )

        fetched = mcp_server.tool_handoff_get({"handoff_id": handoff_id})
        encoded = json.dumps(fetched, ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded, fetched)

        handoff = decoded["handoff"]
        self.assertEqual(handoff["attachments"][0], {"name": "voice.m4a", "path": "/tmp/voice.m4a"})
        self.assertEqual(handoff["context"]["nested"], {"zh": "中文"})
        self.assertEqual(handoff["metrics"], {"bars": 8})
        self.assertEqual(handoff["status"], "done")

    def test_list_summary_survives_a_json_round_trip(self):
        self.create_handoff()
        listed = mcp_server.tool_handoff_list({})
        decoded = json.loads(json.dumps(listed, ensure_ascii=False))
        self.assertEqual(decoded, listed)

    def test_situation_report_survives_a_json_round_trip(self):
        self.create_handoff()
        report = mcp_server.tool_situation_report({})
        decoded = json.loads(json.dumps(report, ensure_ascii=False))
        self.assertEqual(decoded, report)

    def test_non_ascii_instruction_is_stored_verbatim(self):
        instruction = "把这段人声铺成 8 小节 demo，留一点气口"
        created = self.create_handoff(instruction=instruction)
        fetched = mcp_server.tool_handoff_get({"handoff_id": created["handoff"]["id"]})
        self.assertEqual(fetched["handoff"]["instruction"], instruction)
