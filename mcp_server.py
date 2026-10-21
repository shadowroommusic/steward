#!/usr/bin/env python3
"""steward — the ledger behind the floating orb (the app's butler).

一个只增不改的事件账本 + 一套可持久、可幂等、可追踪的交接协议。设计契约见
`~/Documents/ShadowRoom/HANDOFF-for-zcode.md` 任务 13：
工作区**不许**直接写账本，只能 `report_done` 交回执；管家用 `situation_report` / `recent_events` 读。
纯标准库（sqlite3 + json），因为桥按 `python3 mcp_server.py` 拉起它，机器上 python 3.9 就够。

为什么是账本而不是"把对话都存下来"：管家要的是**事件与状态**（谁、做了什么、结果、文件、数字），
逐字稿会让它的上下文几天就爆掉。`injectable` 这一列是闸门——进度类事件只进账本、不进简报。

为什么交接（handoff）另起一张表而不是复用 events：events 是"**发生过**的事"（只增不改的历史），
handoff 是"**还没做完**的事"（有待认领、有归属、有终态、要能被重复点击去重）。事件是过去，handoff 是现在，
一张表就会出现"这行到底是发生过还是没做"的二义——所以两张表，两个真相。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(os.environ.get("SHADOWROOM_STEWARD_DB", Path.home() / "Documents/ShadowRoom/_steward/ledger.db"))

# --------------------------------------------------------------------------------------
# schema：按版本号顺序迁移，重复跑不改变任何东西（幂等迁移）。
# --------------------------------------------------------------------------------------
SCHEMA_VERSION = 2

MIGRATIONS = (
    (
        1,
        (
            """CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  type TEXT NOT NULL,
  actor TEXT NOT NULL,
  object_id TEXT,
  outcome TEXT,
  file TEXT,
  metrics TEXT,
  injectable INTEGER NOT NULL DEFAULT 1
)""",
            "CREATE TABLE IF NOT EXISTS cursor (k TEXT PRIMARY KEY, v INTEGER NOT NULL)",
        ),
    ),
    (
        2,
        (
            """CREATE TABLE IF NOT EXISTS handoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  target TEXT NOT NULL,
  instruction TEXT NOT NULL,
  attachments_json TEXT NOT NULL DEFAULT '[]',
  source_session_id TEXT,
  worker_session_id TEXT,
  context_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  claimed_at INTEGER,
  finished_at INTEGER,
  outcome TEXT,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  error TEXT
)""",
            "CREATE INDEX IF NOT EXISTS idx_handoffs_status ON handoffs(status)",
            "CREATE INDEX IF NOT EXISTS idx_handoffs_target ON handoffs(target)",
            "CREATE INDEX IF NOT EXISTS idx_handoffs_created ON handoffs(created_at DESC)",
        ),
    ),
)

# --------------------------------------------------------------------------------------
# handoff 协议常量：边界写死在这里，工具层不再各自解释一遍。
# --------------------------------------------------------------------------------------
HANDOFF_TARGETS = ("studio", "dj", "library")
HANDOFF_STATUSES = ("pending", "claimed", "running", "done", "failed", "cancelled")
TERMINAL_STATUSES = ("done", "failed", "cancelled")
ACTIVE_STATUSES = ("pending", "claimed", "running")

MAX_INSTRUCTION = 4000
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_NAME = 255
MAX_ATTACHMENT_PATH = 4096
MAX_CONTEXT_BYTES = 8192
MAX_IDEMPOTENCY_KEY = 128
MAX_SESSION_ID = 200
MAX_OUTCOME = 512
MAX_ERROR = 2000
MAX_METRICS_KEYS = 8
MAX_SUMMARY_CHARS = 80
MAX_LIST_LIMIT = 50
DEFAULT_LIST_LIMIT = 10
ACTIVE_SUMMARY_LIMIT = 5
RECENT_SUMMARY_LIMIT = 5

HANDOFF_COLUMNS = (
    "id, idempotency_key, target, instruction, attachments_json, source_session_id, "
    "worker_session_id, context_json, status, attempt, created_at, claimed_at, finished_at, "
    "outcome, metrics_json, error"
)


def now_ms() -> int:
    return int(time.time() * 1000)


def migrate(connection: sqlite3.Connection) -> int:
    """把库迁到最新版本。幂等：已经是最新就直接返回，不重跑任何 DDL。"""
    connection.isolation_level = None
    connection.execute("PRAGMA busy_timeout = 30000")
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    for version, statements in MIGRATIONS:
        if version <= current:
            continue
        # IMMEDIATE：两个进程同时启动时只有一个在改 schema，另一个等它提交。
        connection.execute("BEGIN IMMEDIATE")
        try:
            # 拿到写锁之后再看一眼版本：刚才等锁的时候别人可能已经迁完了。
            inside = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version <= inside:
                connection.execute("COMMIT")
                current = inside
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {int(version)}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        current = version
    return current


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    migrate(connection)
    return connection


def add_event(connection, *, type_, actor, object_id=None, outcome=None, file=None, metrics=None, injectable=True) -> int:
    cursor = connection.execute(
        "INSERT INTO events (ts, type, actor, object_id, outcome, file, metrics, injectable) VALUES (?,?,?,?,?,?,?,?)",
        (int(time.time() * 1000), type_, actor, object_id, outcome, file, json.dumps(metrics or {}, ensure_ascii=False), 1 if injectable else 0),
    )
    connection.commit()
    return int(cursor.lastrowid)


def as_event(row) -> dict:
    return {
        "id": row[0], "ts": row[1], "type": row[2], "actor": row[3],
        "payload": {k: v for k, v in {"object_id": row[4], "outcome": row[5], "file": row[6], "metrics": json.loads(row[7] or "{}")}.items() if v not in (None, {})},
        "injectable": bool(row[8]),
    }


def tool_report_done(args: dict) -> dict:
    """工作区的回执：一行讲清干了什么、结果、文件、最多 3 个数字。"""
    connection = connect()
    metrics = args.get("metrics") or {}
    if len(metrics) > 3:
        metrics = dict(list(metrics.items())[:3])
    event_id = add_event(
        connection,
        type_=str(args.get("type") or "job.done"),
        actor=str(args.get("actor") or "unknown"),
        object_id=args.get("object_id"),
        outcome=str(args.get("outcome") or "ok"),
        file=args.get("file"),
        metrics=metrics,
        injectable=bool(args.get("injectable", True)),
    )
    return {"event_id": event_id}


def tool_recent_events(args: dict) -> dict:
    connection = connect()
    limit = max(1, min(int(args.get("limit") or 20), 200))
    since = int(args.get("since_id") or 0)
    rows = connection.execute(
        "SELECT id, ts, type, actor, object_id, outcome, file, metrics, injectable FROM events WHERE id > ? ORDER BY id DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return {"events": [as_event(row) for row in reversed(rows)]}


def handoff_section(connection) -> dict:
    """管家眼里"还没干完 / 刚干完"的活：数量 + 有界摘要。

    摘要里**不放**附件内容、也不放整段 instruction——只放一行预览，
    否则简报会被一次长指令顶爆（账本的第一原则：只存指针，不存内容）。
    """
    counts = {status: 0 for status in HANDOFF_STATUSES}
    for row in connection.execute("SELECT status, COUNT(*) AS n FROM handoffs GROUP BY status").fetchall():
        counts[row["status"]] = int(row["n"])
    active = connection.execute(
        f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE status IN ('pending','claimed','running') "
        "ORDER BY created_at ASC, id ASC LIMIT ?",
        (ACTIVE_SUMMARY_LIMIT,),
    ).fetchall()
    recent = connection.execute(
        f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE status IN ('done','failed','cancelled') "
        "ORDER BY COALESCE(finished_at, created_at) DESC, id DESC LIMIT ?",
        (RECENT_SUMMARY_LIMIT,),
    ).fetchall()
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "active": [as_handoff_summary(row) for row in active],
        "recent": [as_handoff_summary(row) for row in recent],
    }


def tool_situation_report(args: dict) -> dict:
    """随时汇报：最近的关键事件 + 未消化的问题 + 谁在动。"""
    connection = connect()
    rows = connection.execute(
        "SELECT id, ts, type, actor, object_id, outcome, file, metrics, injectable FROM events WHERE injectable = 1 ORDER BY id DESC LIMIT 10"
    ).fetchall()
    events = [as_event(row) for row in reversed(rows)]
    errors = [event for event in events if event["payload"].get("outcome") == "failed"]
    actors = sorted({event["actor"] for event in events})
    last = events[-1] if events else None
    handoffs = handoff_section(connection)
    pending = handoffs["counts"]["pending"] + handoffs["counts"]["claimed"] + handoffs["counts"]["running"]
    if last is None:
        summary = "还没有任何事件"
    else:
        summary = f"{len(events)} 条关键事件，最近一条：{last['type']}（{last['actor']}）"
    if pending:
        summary += f"；{pending} 个交接待办"
    return {
        "summary": summary,
        "actors": actors,
        "errors": errors,
        "events": events,
        "handoffs": handoffs,
        "last_injected_event_id": last["id"] if last else 0,
    }


def tool_focus_change(args: dict) -> dict:
    """切页面/切工程/选中片段 —— 让管家"知道你现在在哪"。"""
    connection = connect()
    event_id = add_event(
        connection,
        type_="focus.change",
        actor=str(args.get("actor") or "ui"),
        object_id=args.get("object_id"),
        outcome="ok",
        metrics={"workspace": args.get("workspace")} if args.get("workspace") else None,
        injectable=True,
    )
    return {"event_id": event_id}


def tool_route_intent(args: dict) -> dict:
    """Classify, but never execute, a cross-page request from the global butler."""
    allowed = {"studio", "dj", "library", "butler_direct", "ask_user"}
    target = str(args.get("target") or "ask_user")
    if target not in allowed:
        raise ValueError(f"target must be one of: {', '.join(sorted(allowed))}")
    confidence = max(0.0, min(1.0, float(args.get("confidence") or 0)))
    required = [str(item) for item in (args.get("required_context") or []) if str(item).strip()]
    context = args.get("context") if isinstance(args.get("context"), dict) else {}
    satisfied = True
    missing: list[str] = []
    if target == "studio":
        if context.get("workspace") != "studio":
            missing.append("studio")
        if not context.get("projectId"):
            missing.append("projectId")
    elif target == "dj":
        if context.get("workspace") != "dj":
            missing.append("dj")
        if not context.get("setId"):
            missing.append("setId")
    elif target == "library":
        if context.get("workspace") != "library":
            missing.append("library")
    satisfied = not missing
    return {
        "accept": True,
        "target": target,
        "action_summary": str(args.get("action_summary") or "").strip(),
        "required_context": required,
        "confidence": confidence,
        "context_satisfied": satisfied,
        "missing_context": missing,
    }


# --------------------------------------------------------------------------------------
# handoff：管家 → Studio / DJ / 曲库 的可持久交接。
# --------------------------------------------------------------------------------------
def read_required_text(value, field: str, limit: int) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    return text


def read_optional_text(value, field: str, limit: int):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    return text


def read_handoff_id(value) -> int:
    try:
        handoff_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("handoff_id must be an integer")
    if handoff_id <= 0:
        raise ValueError("handoff_id must be positive")
    return handoff_id


def read_attachments(raw) -> list:
    if raw is None or raw == "":
        return []
    if not isinstance(raw, list):
        raise ValueError("attachments must be an array of {name, path}")
    if len(raw) > MAX_ATTACHMENTS:
        raise ValueError(f"attachments must have at most {MAX_ATTACHMENTS} items")
    attachments = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each attachment must be an object with name and path")
        name = read_required_text(item.get("name"), "attachment name", MAX_ATTACHMENT_NAME)
        path = read_required_text(item.get("path"), "attachment path", MAX_ATTACHMENT_PATH)
        attachments.append({"name": name, "path": path})
    return attachments


def read_context(raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("context must be an object")
    encoded = json.dumps(raw, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError(f"context is larger than {MAX_CONTEXT_BYTES} bytes")
    return raw


def read_metrics(raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("metrics must be an object")
    if len(raw) > MAX_METRICS_KEYS:
        raise ValueError(f"metrics must have at most {MAX_METRICS_KEYS} keys")
    try:
        json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValueError("metrics must be JSON-serializable")
    return raw


def as_handoff(row) -> dict:
    """一整条交接（带附件与上下文）——只有 handoff_get / create / claim / finish 返回它。"""
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "target": row["target"],
        "instruction": row["instruction"],
        "attachments": json.loads(row["attachments_json"] or "[]"),
        "source_session_id": row["source_session_id"],
        "worker_session_id": row["worker_session_id"],
        "context": json.loads(row["context_json"] or "{}"),
        "status": row["status"],
        "attempt": row["attempt"],
        "created_at": row["created_at"],
        "claimed_at": row["claimed_at"],
        "finished_at": row["finished_at"],
        "outcome": row["outcome"],
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "error": row["error"],
    }


def as_handoff_summary(row) -> dict:
    """有界摘要：给列表与简报用。没有附件内容，instruction 只留一行预览。"""
    instruction = row["instruction"] or ""
    preview = instruction
    if len(preview) > MAX_SUMMARY_CHARS:
        preview = preview[:MAX_SUMMARY_CHARS] + "…"
    return {
        "id": row["id"],
        "target": row["target"],
        "status": row["status"],
        "attempt": row["attempt"],
        "created_at": row["created_at"],
        "claimed_at": row["claimed_at"],
        "finished_at": row["finished_at"],
        "instruction_preview": preview,
        "attachment_count": len(json.loads(row["attachments_json"] or "[]")),
    }


def fetch_handoff(connection, handoff_id: int):
    return connection.execute(f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()


def tool_handoff_create(args: dict) -> dict:
    """建一条交接。同一 idempotency_key 再点一次只会拿到同一条，不会重复建。"""
    target = str(args.get("target") or "").strip().lower()
    if target not in HANDOFF_TARGETS:
        raise ValueError(f"target must be one of: {', '.join(HANDOFF_TARGETS)}")
    instruction = read_required_text(args.get("instruction"), "instruction", MAX_INSTRUCTION)
    idempotency_key = read_required_text(args.get("idempotency_key"), "idempotency_key", MAX_IDEMPOTENCY_KEY)
    attachments = read_attachments(args.get("attachments"))
    source_session_id = read_optional_text(args.get("source_session_id"), "source_session_id", MAX_SESSION_ID)
    context = read_context(args.get("context"))
    attachments_json = json.dumps(attachments, ensure_ascii=False)

    connection = connect()
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO handoffs
               (idempotency_key, target, instruction, attachments_json, source_session_id, context_json,
                status, attempt, created_at)
               VALUES (?,?,?,?,?,?, 'pending', 0, ?)""",
            (idempotency_key, target, instruction, attachments_json, source_session_id,
             json.dumps(context, ensure_ascii=False), now_ms()),
        )
        created = cursor.rowcount == 1
        row = connection.execute(
            f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    same_payload = (
        row["target"] == target
        and row["instruction"] == instruction
        and json.loads(row["attachments_json"] or "[]") == attachments
    )
    return {
        "handoff": as_handoff(row),
        "created": created,
        "duplicate": not created,
        # 同一个 key 配不同的活 = 调用方把 key 用错了；如实说出来，但**不覆盖**已存在的那条。
        "conflict": (not created) and not same_payload,
    }


def tool_handoff_claim(args: dict) -> dict:
    """认领一条交接：pending → claimed。被别人先认领就如实说 claimed=false。"""
    handoff_id = read_handoff_id(args.get("handoff_id"))
    worker_session_id = read_required_text(args.get("worker_session_id"), "worker_session_id", MAX_SESSION_ID)

    connection = connect()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = fetch_handoff(connection, handoff_id)
        if row is None:
            raise ValueError(f"handoff {handoff_id} does not exist")
        claimed = False
        if row["status"] == "pending":
            connection.execute(
                """UPDATE handoffs SET status = 'claimed', worker_session_id = ?, claimed_at = ?,
                   attempt = attempt + 1 WHERE id = ? AND status = 'pending'""",
                (worker_session_id, now_ms(), handoff_id),
            )
            claimed = True
        elif row["status"] in ("claimed", "running") and row["worker_session_id"] == worker_session_id:
            # 同一个 worker 再点一次 = "我开工了"：claimed → running；已经在 running 就原样返回。
            if row["status"] == "claimed":
                connection.execute(
                    "UPDATE handoffs SET status = 'running' WHERE id = ? AND status = 'claimed'", (handoff_id,)
                )
            claimed = True
        row = fetch_handoff(connection, handoff_id)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "handoff_id": handoff_id,
        "claimed": claimed,
        "status": row["status"],
        "worker_session_id": row["worker_session_id"],
        "attempt": row["attempt"],
        "handoff": as_handoff(row),
    }


def tool_handoff_finish(args: dict) -> dict:
    """交回收据。只有认领它的 worker 能写终态；终态幂等，非法回退拒绝。"""
    handoff_id = read_handoff_id(args.get("handoff_id"))
    worker_session_id = read_required_text(args.get("worker_session_id"), "worker_session_id", MAX_SESSION_ID)
    status = str(args.get("status") or "").strip().lower()
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(TERMINAL_STATUSES)}")
    outcome = read_optional_text(args.get("outcome"), "outcome", MAX_OUTCOME)
    error = read_optional_text(args.get("error"), "error", MAX_ERROR)
    metrics = read_metrics(args.get("metrics"))

    connection = connect()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = fetch_handoff(connection, handoff_id)
        if row is None:
            raise ValueError(f"handoff {handoff_id} does not exist")
        if row["status"] in TERMINAL_STATUSES:
            if row["status"] != status:
                raise ValueError(
                    f"handoff {handoff_id} already finished as {row['status']}; refusing to rewrite it as {status}"
                )
            connection.execute("COMMIT")
            return {
                "handoff_id": handoff_id,
                "ok": True,
                "idempotent": True,
                "status": row["status"],
                "handoff": as_handoff(row),
            }
        if row["status"] == "pending":
            raise ValueError(f"handoff {handoff_id} must be claimed before it can finish")
        if row["worker_session_id"] != worker_session_id:
            raise ValueError(f"handoff {handoff_id} is owned by another worker")
        connection.execute(
            """UPDATE handoffs SET status = ?, finished_at = ?, outcome = ?, metrics_json = ?, error = ?
               WHERE id = ?""",
            (status, now_ms(), outcome, json.dumps(metrics, ensure_ascii=False), error, handoff_id),
        )
        row = fetch_handoff(connection, handoff_id)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "handoff_id": handoff_id,
        "ok": True,
        "idempotent": False,
        "status": status,
        "handoff": as_handoff(row),
    }


def tool_handoff_get(args: dict) -> dict:
    handoff_id = read_handoff_id(args.get("handoff_id"))
    connection = connect()
    row = fetch_handoff(connection, handoff_id)
    if row is None:
        raise ValueError(f"handoff {handoff_id} does not exist")
    return {"handoff": as_handoff(row)}


def tool_handoff_list(args: dict) -> dict:
    """按状态 / 目标列出交接摘要（有界）。"""
    limit = max(1, min(int(args.get("limit") or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
    status = str(args.get("status") or "").strip().lower()
    target = str(args.get("target") or "").strip().lower()
    if status and status not in HANDOFF_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(HANDOFF_STATUSES)}")
    if target and target not in HANDOFF_TARGETS:
        raise ValueError(f"target must be one of: {', '.join(HANDOFF_TARGETS)}")
    where = []
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if target:
        where.append("target = ?")
        params.append(target)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    connection = connect()
    total = int(connection.execute(f"SELECT COUNT(*) FROM handoffs{clause}", params).fetchone()[0])
    rows = connection.execute(
        f"SELECT {HANDOFF_COLUMNS} FROM handoffs{clause} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return {
        "handoffs": [as_handoff_summary(row) for row in rows],
        "count": len(rows),
        "total": total,
    }


TOOLS = {
    "report_done": (tool_report_done, "一个工作区干完活后的回执（谁、什么、结果、文件、≤3 个数字）。"),
    "recent_events": (tool_recent_events, "按 id 拉事件明细（管家要细节时才用）。"),
    "situation_report": (tool_situation_report, "随时汇报：最近关键事件、未解决的问题、活跃的工作区。"),
    "focus_change": (tool_focus_change, "记录焦点变化（切页面/切工程/选中片段）。"),
    "route_intent": (tool_route_intent, "全局管家分类一次请求应交给 Studio、DJ、曲库还是自己回答；纯分类，绝不执行副作用。"),
    "handoff_create": (tool_handoff_create, "建一条可持久、可幂等的交接：同一个 idempotency_key 重复提交只会得到同一条 handoff。"),
    "handoff_claim": (tool_handoff_claim, "认领一条交接（pending→claimed）；被别人先认领就返回当前状态与 claimed=false。"),
    "handoff_finish": (tool_handoff_finish, "交回交接终态（done/failed/cancelled）；只有认领者能写，终态幂等，非法回退拒绝。"),
    "handoff_get": (tool_handoff_get, "按 id 取一条交接的完整状态（含附件与上下文）。"),
    "handoff_list": (tool_handoff_list, "按状态/目标列出交接摘要（有界，不含附件内容）。"),
}
SCHEMAS = {
    "report_done": {"type": "object", "properties": {"job_id": {"type": "string"}, "type": {"type": "string"}, "actor": {"type": "string"}, "object_id": {"type": "string"}, "outcome": {"type": "string"}, "file": {"type": "string"}, "metrics": {"type": "object"}, "injectable": {"type": "boolean"}}, "required": ["actor"]},
    "recent_events": {"type": "object", "properties": {"since_id": {"type": "integer"}, "limit": {"type": "integer"}}},
    "situation_report": {"type": "object", "properties": {"workspace_id": {"type": "string"}}},
    "focus_change": {"type": "object", "properties": {"workspace": {"type": "string"}, "object_id": {"type": "string"}, "object_type": {"type": "string"}, "actor": {"type": "string"}}},
    "route_intent": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["studio", "dj", "library", "butler_direct", "ask_user"]},
            "action_summary": {"type": "string"},
            "required_context": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "context": {"type": "object"},
        },
        "required": ["target", "action_summary", "confidence"],
    },
    "handoff_create": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": list(HANDOFF_TARGETS)},
            "instruction": {"type": "string"},
            "attachments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["name", "path"],
                },
            },
            "source_session_id": {"type": "string"},
            "context": {"type": "object"},
            "idempotency_key": {"type": "string"},
        },
        "required": ["target", "instruction", "idempotency_key"],
    },
    "handoff_claim": {
        "type": "object",
        "properties": {"handoff_id": {"type": "integer"}, "worker_session_id": {"type": "string"}},
        "required": ["handoff_id", "worker_session_id"],
    },
    "handoff_finish": {
        "type": "object",
        "properties": {
            "handoff_id": {"type": "integer"},
            "worker_session_id": {"type": "string"},
            "status": {"type": "string", "enum": list(TERMINAL_STATUSES)},
            "outcome": {"type": "string"},
            "metrics": {"type": "object"},
            "error": {"type": "string"},
        },
        "required": ["handoff_id", "worker_session_id", "status"],
    },
    "handoff_get": {
        "type": "object",
        "properties": {"handoff_id": {"type": "integer"}},
        "required": ["handoff_id"],
    },
    "handoff_list": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": list(HANDOFF_STATUSES)},
            "target": {"type": "string", "enum": list(HANDOFF_TARGETS)},
            "limit": {"type": "integer"},
        },
    },
}


def handle(request: dict):
    method = request.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "steward", "version": "0.2.0"}}
    if method == "tools/list":
        return {"tools": [{"name": name, "description": doc, "inputSchema": SCHEMAS[name]} for name, (_, doc) in TOOLS.items()]}
    if method == "tools/call":
        name = request["params"]["name"]
        if name not in TOOLS:
            raise ValueError(f"unknown tool {name}")
        result = TOOLS[name][0](request["params"].get("arguments") or {})
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}
    raise ValueError(f"unknown method {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if line == "":
            continue
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": handle(request)}, ensure_ascii=False) + "\n")
        except Exception as cause:  # 一个工具炸了不该带走整个插件
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000, "message": str(cause)}}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
