# Steward（管家账本）

The ledger behind the floating orb: every workspace reports what it did, the orb reads the
report. An append-only event log plus ten MCP tools — five for the ledger, five for durable,
idempotent handoffs — no chat transcripts, no models, no dependencies beyond the standard
library.

悬浮球（管家）背后的账本：每个工作区干完活交一行回执，管家随时汇报；管家要把活交到
Studio / DJ / 曲库，就写一条**可持久、可幂等、可追踪**的交接，别再只剩一段草稿。
一张只增不改的事件表 + 十个 MCP 工具（五个记账、五个交接），纯标准库。

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
| `situation_report` | `workspace_id?` | What is going on right now: recent key events, unresolved failures, which actors are moving, the bounded handoff section, plus `last_injected_event_id`. |
| `focus_change` | `workspace?`, `object_id?`, `object_type?`, `actor?` | Page / project / selection changes are events too — that is how the orb knows where you are after you switch. |
| `app_command` | `op` (`navigate`\|`dispatch`, required), `view?`, `route?`, `reason?` | The butler's one typed command entry. `navigate` changes only the active app view; `dispatch` validates a Studio / DJ / library task. **Pure validation — never writes anything.** |
| `handoff_create` | `target` (`studio`\|`dj`\|`library`), `instruction`, `idempotency_key` (required), `attachments?`, `source_session_id?`, `context?` | Create a durable handoff. The same `idempotency_key` always returns the same handoff — a double click never creates a second one. |
| `handoff_claim` | `handoff_id`, `worker_session_id` (required) | Take ownership: `pending` → `claimed`. If another worker got there first, you get the current state and `claimed: false`. |
| `handoff_finish` | `handoff_id`, `worker_session_id`, `status` (`done`\|`failed`\|`cancelled`) (required), `outcome?`, `metrics?`, `error?` | Close a handoff. Only the claiming worker may write; the terminal state is idempotent; an illegal rewrite is refused. |
| `handoff_get` | `handoff_id` (required) | One handoff in full — instruction, attachments, context, owner, timings. |
| `handoff_list` | `status?`, `target?`, `limit?` (1–50, default 10) | Bounded list of handoff summaries. |

## Handoffs

`app_command` says *what the user wants*. A handoff is a dispatch **handed over**: it
survives a reload, it remembers which worker owns it, and it can be asked about again later.

```
pending ──claim──▶ claimed ──claim(same worker)──▶ running ──finish──▶ done | failed | cancelled
```

- **Persistent.** The handoff lives in SQLite next to the ledger, with its attachments,
  context and owner. Closing the window does not lose it.
- **Idempotent.** `idempotency_key` is unique. Creating the same handoff again returns the
  existing one (`created: false`, `duplicate: true`) instead of a second row. If the same key
  arrives with a *different* payload the call reports `conflict: true` and the stored handoff
  is left untouched — a mis-keyed request never overwrites real work.
- **Traceable.** Exactly one worker can own a handoff: the claim is a single atomic
  `pending → claimed` transition. The loser of a race is told the truth
  (`claimed: false`, current status, current owner) instead of silently double-running the job.
- **Terminal states are final.** `done` / `failed` / `cancelled` are idempotent for the same
  status and refuse to be rewritten into a different one; an unclaimed handoff cannot be
  finished, and a worker that did not claim it cannot write a result.

### Limits (enforced, not documented-and-hoped)

| Field | Limit |
| --- | --- |
| `instruction` | 4 000 characters |
| `attachments` | 20 items; name ≤ 255, path ≤ 4 096 characters |
| `context` | 8 192 bytes of JSON |
| `idempotency_key` | 128 characters |
| `outcome` / `error` | 512 / 2 000 characters |
| `metrics` | 8 keys |

`handoff_list` and `situation_report` return **summaries**: one-line instruction preview,
attachment *count*, no attachment paths. The butler's brief stays bounded — the ledger's
first rule is to store pointers, not content.

## The contract

- **Workspaces never write the ledger directly.** They call `report_done` and hand back a
  receipt. One writer, one schema, no two code paths disagreeing about what an event means.
- **Append-only.** No updates, no deletes in the tool surface; history is the point.
- **Bounded injection.** `metrics` keeps the first three keys; `injectable=false` marks
  progress events that belong in the log but not in the brief.
- **Events and handoffs are two truths.** Events are what *happened*; handoffs are what is
  *still to be done*. One table would make every row ambiguous, so there are two.
- **`app_command` never has side effects.** It validates a closed enum of app actions; creating or claiming a handoff is
  a separate, explicit step.

## Wire it up

```sh
# stdio MCP server, no third-party packages
python3 mcp_server.py
```

Storage: `~/Documents/ShadowRoom/_steward/ledger.db` (`SHADOWROOM_STEWARD_DB` overrides it).
Delete that file to reset the ledger; nothing else references it.

Schema migrations are versioned (`PRAGMA user_version`) and idempotent: the server migrates
on connect, so an older database gains the `handoffs` table on first use without losing a
single event.

## Development

```sh
python3 -c "import ast,sys; ast.parse(open('mcp_server.py').read())"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp_server.py
python3 -m unittest discover -s tests -q
```
