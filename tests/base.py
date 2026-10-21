"""单测的公共底座：一份账本一个测试，跑完就删。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_server  # noqa: E402  （路径必须先补上）


class StewardTestCase(unittest.TestCase):
    """每个测试一份全新的库：真账本一次都不会被碰。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="steward-test-")
        self.addCleanup(self._tmp.cleanup)
        self._previous_db = mcp_server.DB
        mcp_server.DB = Path(self._tmp.name) / "ledger.db"
        self.addCleanup(self._restore_db)

    def _restore_db(self) -> None:
        mcp_server.DB = self._previous_db

    # -- 小工具 ------------------------------------------------------------------
    def create_handoff(self, **overrides):
        args = {
            "target": "studio",
            "instruction": "把这段旋律铺成 8 小节 demo",
            "idempotency_key": "route-1",
        }
        args.update(overrides)
        return mcp_server.tool_handoff_create(args)

    def count_rows(self, table: str) -> int:
        connection = mcp_server.connect()
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()

    def fetch_handoff_row(self, handoff_id: int):
        connection = mcp_server.connect()
        try:
            return connection.execute(
                "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
        finally:
            connection.close()
