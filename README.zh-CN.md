# Steward（管家账本）

> **支持平台：** macOS 已支持（本机开发与验证）｜Windows 未验证（代码已做路径兼容，但未在真机测试）。

悬浮球（管家）背后的账本：每个工作区干完活交一行回执，管家随时汇报；管家要把活交到
Studio / DJ / 曲库，就写一条**可持久、可幂等、可追踪**的交接，别再只剩一段草稿。
一张只增不改的事件表 + 十个 MCP 工具（五个记账、五个交接），纯标准库（sqlite3 + json），
机器上有 python3.9 就能跑。

[English](README.md) · License: [AGPL-3.0-only](LICENSE)

## 为什么是账本，不是逐字稿

管家要回答"现在什么情况"，但不能把每一次对话都塞进上下文。事件能回答这件事：谁、做了什么、
结果、文件、最多三个数字。逐字稿会让上下文几天就爆掉；账本是有界的，而且一直答得出来。

`injectable` 是闸门：进度类事件照写，但不进简报。只有能回答问题的进。

## 工具（MCP，共 10 个）

| 工具 | 输入 | 说明 |
| --- | --- | --- |
| `report_done` | `actor`（必填）、`type?`、`object_id?`、`outcome?`、`file?`、`metrics?`（≤3 个键）、`injectable?` | 工作区的一行回执，返回 `event_id` |
| `recent_events` | `since_id?`、`limit?`（1–200，默认 20） | 按 id 拉明细——管家要细节时才用 |
| `situation_report` | `workspace_id?` | 随时汇报：最近关键事件、未解决的问题、谁在动，加上有界的交接段落，外加 `last_injected_event_id` |
| `focus_change` | `workspace?`、`object_id?`、`object_type?`、`actor?` | 切页面 / 切工程 / 选中片段也是事件——管家因此知道你现在在哪 |
| `route_intent` | `target`、`action_summary`、`confidence`（必填）、`required_context?`、`context?` | 分类这次请求该交给 Studio / DJ / 曲库还是管家自己。**纯分类，不写任何东西** |
| `handoff_create` | `target`（`studio`\|`dj`\|`library`）、`instruction`、`idempotency_key`（必填）、`attachments?`、`source_session_id?`、`context?` | 建一条可持久交接。同一个 `idempotency_key` 永远拿回同一条——重复点击不会建出第二条 |
| `handoff_claim` | `handoff_id`、`worker_session_id`（必填） | 认领：`pending` → `claimed`。被别人先认领就返回当前状态与 `claimed: false` |
| `handoff_finish` | `handoff_id`、`worker_session_id`、`status`（`done`\|`failed`\|`cancelled`）（必填）、`outcome?`、`metrics?`、`error?` | 交回终态。只有认领者能写；终态幂等；非法回退拒绝 |
| `handoff_get` | `handoff_id`（必填） | 一条交接的完整状态：指令、附件、上下文、归属、时间 |
| `handoff_list` | `status?`、`target?`、`limit?`（1–50，默认 10） | 列出交接摘要（有界） |

## 交接（handoff）

`route_intent` 只回答"这是什么请求"。**交接**才是真的把活交出去：刷新页面不丢、
知道归谁、事后还能再问一遍。

```
pending ──claim──▶ claimed ──claim(同一 worker)──▶ running ──finish──▶ done | failed | cancelled
```

- **可持久**：交接与账本存在同一个 SQLite 里，带附件、上下文和归属；关掉窗口也不丢。
- **可幂等**：`idempotency_key` 唯一。同一个 key 再提交一次，返回原来那条
  （`created: false`、`duplicate: true`），不会多出一行。同一 key 带着**不同的**内容再来，
  会如实报 `conflict: true`，并且**不动**已存在的那条——key 用错了也不会覆盖真活。
- **可追踪**：认领是一次原子的 `pending → claimed`。抢输的一方拿到的是实话
  （`claimed: false` + 当前状态 + 当前归属），而不是把同一件活静悄悄地跑两遍。
- **终态就是终态**：`done` / `failed` / `cancelled` 同状态重复写是幂等的，改写成别的终态会被拒；
  没认领的交接不能交回执，没认领它的 worker 也不能写结果。

### 上限（真拦，不是写在文档里）

| 字段 | 上限 |
| --- | --- |
| `instruction` | 4 000 字符 |
| `attachments` | 20 条；name ≤ 255，path ≤ 4 096 字符 |
| `context` | 8 192 字节 JSON |
| `idempotency_key` | 128 字符 |
| `outcome` / `error` | 512 / 2 000 字符 |
| `metrics` | 8 个键 |

`handoff_list` 与 `situation_report` 只返回**摘要**：一行指令预览、附件**条数**，没有附件路径。
简报因此始终有界——账本的第一原则是只存指针，不存内容。

## 契约

- **工作区不直接写账本**，只能调 `report_done` 交回执。一个写入者、一份 schema，不存在两条
  代码路径对"事件"理解不一致。
- **只增不改**：工具层没有 update / delete，历史本身就是价值。
- **有界注入**：`metrics` 只保留前三个键；`injectable=false` 标记"进日志但不进简报"的进度事件。
- **事件与交接待遇是两条真相**：事件是"**发生过**的事"，交接是"**还没做完**的事"。
  放一张表，每一行都会变成"到底是发生过还是没做"的二义。
- **`route_intent` 没有副作用**：它只分类；要真的建一条交接或认领一条交接，是另外一步、显式的动作。

## 怎么接

```sh
# stdio MCP server，不需要任何第三方包
python3 mcp_server.py
```

存储：`~/Documents/ShadowRoom/_steward/ledger.db`（`SHADOWROOM_STEWARD_DB` 可覆盖）。
想重置直接删这个文件，没有别的地方引用它。

迁移按版本号走（`PRAGMA user_version`）且幂等：服务端每次连接都会迁移，
所以老库第一次用上就会长出 `handoffs` 表，而一条事件都不会丢。

## 开发

```sh
python3 -c "import ast,sys; ast.parse(open('mcp_server.py').read())"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp_server.py
python3 -m unittest discover -s tests -q
```
