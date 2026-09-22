# Steward（管家账本）

The ledger behind the floating orb: every workspace reports what it did, the orb reads the
report. An append-only event log plus four MCP tools — no chat transcripts, no models, no
dependencies beyond the standard library.

悬浮球（管家）背后的账本：每个工作区干完活交一行回执，管家随时汇报。只增不改的事件表 +
四个 MCP 工具，纯标准库。

> **Platform support:** macOS — supported (developed & verified on this machine) · Windows — unverified (paths are cross-platform in the codebase, but no real-machine testing yet).

[中文说明](README.zh-CN.md) · License: [AGPL-3.0-only](LICENSE)

## Why a ledger instead of a transcript

The orb has to answer "what is going on right now?" without holding every conversation in
context. Events do that: who, what, outcome, file, at most three numbers. A transcript grows
until the context window dies; a ledger stays bounded and stays answerable.

The `injectable` column is the gate: progress chatter is written down but never reaches the
brief. Only events that answer a question get injected.

## Tools (MCP)

| Tool | Input | What it does |
| --- | --- | --- |
| `report_done` | `actor` (required), `type?`, `object_id?`, `outcome?`, `file?`, `metrics?` (≤3 keys), `injectable?` | A workspace's one-line receipt. Returns `event_id`. |
| `recent_events` | `since_id?`, `limit?` (1–200, default 20) | Pull event detail on demand — the orb asks for specifics. |
| `situation_report` | `workspace_id?` | What is going on right now: recent key events, unresolved failures, which actors are moving, plus `last_injected_event_id`. |
| `focus_change` | `workspace?`, `object_id?`, `object_type?`, `actor?` | Page / project / selection changes are events too — that is how the orb knows where you are after you switch. |

## The contract

- **Workspaces never write the ledger directly.** They call `report_done` and hand back a
  receipt. One writer, one schema, no two code paths disagreeing about what an event means.
- **Append-only.** No updates, no deletes in the tool surface; history is the point.
- **Bounded injection.** `metrics` keeps the first three keys; `injectable=false` marks
  progress events that belong in the log but not in the brief.

## Wire it up

```sh
# stdio MCP server, no third-party packages
python3 mcp_server.py
```

Storage: `~/Documents/ShadowRoom/_steward/ledger.db` (`SHADOWROOM_STEWARD_DB` overrides it).
Delete that file to reset the ledger; nothing else references it.

## Development

```sh
python3 -c "import ast,sys; ast.parse(open('mcp_server.py').read())"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp_server.py
```
