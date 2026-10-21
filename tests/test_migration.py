"""迁移：幂等、只加不改、老的账本原样留下。"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import StewardTestCase  # noqa: E402

import mcp_server  # noqa: E402


class MigrationTest(StewardTestCase):
    def test_fresh_database_lands_on_current_version(self):
        connection = mcp_server.connect()
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()
        self.assertEqual(version, mcp_server.SCHEMA_VERSION)

    def test_migration_is_idempotent(self):
        # 连着连三次：版本不动、表不动、数据不动。
        for _ in range(3):
            mcp_server.connect().close()
        connection = mcp_server.connect()
        try:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                mcp_server.SCHEMA_VERSION,
            )
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("events", tables)
            self.assertIn("handoffs", tables)
        finally:
            connection.close()

    def test_v1_database_keeps_its_events_and_gains_handoffs(self):
        """一个只建过 events 的老库（user_version=1）迁上来：老数据一行不少。"""
        connection = sqlite3.connect(mcp_server.DB)
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts INTEGER NOT NULL,
                  type TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  object_id TEXT,
                  outcome TEXT,
                  file TEXT,
                  metrics TEXT,
                  injectable INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS cursor (k TEXT PRIMARY KEY, v INTEGER NOT NULL);
                INSERT INTO events (ts, type, actor, outcome) VALUES (1, 'job.done', 'legacy', 'ok');
                PRAGMA user_version = 1;
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = mcp_server.connect()
        try:
            self.assertEqual(
                int(migrated.execute("PRAGMA user_version").fetchone()[0]),
                mcp_server.SCHEMA_VERSION,
            )
            rows = migrated.execute("SELECT type, actor FROM events").fetchall()
            self.assertEqual([(row["type"], row["actor"]) for row in rows], [("job.done", "legacy")])
            tables = {
                row["name"]
                for row in migrated.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("handoffs", tables)
        finally:
            migrated.close()

    def test_migration_never_drops_handoff_rows(self):
        handoff = self.create_handoff()
        for _ in range(3):
            mcp_server.connect().close()
        again = mcp_server.tool_handoff_get({"handoff_id": handoff["handoff"]["id"]})
        self.assertEqual(again["handoff"]["instruction"], handoff["handoff"]["instruction"])
        self.assertEqual(self.count_rows("handoffs"), 1)
