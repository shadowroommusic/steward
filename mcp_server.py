#!/usr/bin/env python3
"""steward — the ledger behind the floating orb (the app's butler).

一个只增不改的事件账本 + 四个工具。设计契约见 `~/Documents/ShadowRoom/HANDOFF-for-zcode.md` 任务 13：
工作区**不许**直接写账本，只能 `report_done` 交回执；管家用 `situation_report` / `recent_events` 读。
纯标准库（sqlite3 + json），因为桥按 `python3 mcp_server.py` 拉起它，机器上 python 3.9 就够。

为什么是账本而不是"把对话都存下来"：管家要的是**事件与状态**（谁、做了什么、结果、文件、数字），
逐字稿会让它的上下文几天就爆掉。`injectable` 这一列是闸门——进度类事件只进账本、不进简报。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(os.environ.get("SHADOWROOM_STEWARD_DB", Path.home() / "Documents/ShadowRoom/_steward/ledger.db"))
SCHEMA = """
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
"""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB)
    connection.executescript(SCHEMA)
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
    return {
        "summary": "还没有任何事件" if last is None else f"{len(events)} 条关键事件，最近一条：{last['type']}（{last['actor']}）",
        "actors": actors,
        "errors": errors,
        "events": events,
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


TOOLS = {
    "report_done": (tool_report_done, "一个工作区干完活后的回执（谁、什么、结果、文件、≤3 个数字）。"),
    "recent_events": (tool_recent_events, "按 id 拉事件明细（管家要细节时才用）。"),
    "situation_report": (tool_situation_report, "随时汇报：最近关键事件、未解决的问题、活跃的工作区。"),
    "focus_change": (tool_focus_change, "记录焦点变化（切页面/切工程/选中片段）。"),
    "route_intent": (tool_route_intent, "全局管家分类一次请求应交给 Studio、DJ、曲库还是自己回答；纯分类，绝不执行副作用。"),
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
}


def handle(request: dict):
    method = request.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "steward", "version": "0.1.0"}}
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
