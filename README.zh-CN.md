# Steward（管家账本）

> **支持平台：** macOS 已支持（本机开发与验证）｜Windows 未验证（代码已做路径兼容，但未在真机测试）。

悬浮球（管家）背后的账本：每个工作区干完活交一行回执，管家随时汇报。一张只增不改的事件表 +
四个 MCP 工具，纯标准库（sqlite3 + json），机器上有 python3.9 就能跑。

[English](README.md) · License: [AGPL-3.0-only](LICENSE)

## 为什么是账本，不是逐字稿

管家要回答"现在什么情况"，但不能把每一次对话都塞进上下文。事件能回答这件事：谁、做了什么、
结果、文件、最多三个数字。逐字稿会让上下文几天就爆掉；账本是有界的，而且一直答得出来。

`injectable` 是闸门：进度类事件照写，但不进简报。只有能回答问题的进。

## 工具（MCP，共 4 个）

| 工具 | 输入 | 说明 |
| --- | --- | --- |
| `report_done` | `actor`（必填）、`type?`、`object_id?`、`outcome?`、`file?`、`metrics?`（≤3 个键）、`injectable?` | 工作区的一行回执，返回 `event_id` |
| `recent_events` | `since_id?`、`limit?`（1–200，默认 20） | 按 id 拉明细——管家要细节时才用 |
| `situation_report` | `workspace_id?` | 随时汇报：最近关键事件、未解决的问题、谁在动，外加 `last_injected_event_id` |
| `focus_change` | `workspace?`、`object_id?`、`object_type?`、`actor?` | 切页面 / 切工程 / 选中片段也是事件——管家因此知道你现在在哪 |

## 契约

- **工作区不直接写账本**，只能调 `report_done` 交回执。一个写入者、一份 schema，不存在两条
  代码路径对"事件"理解不一致。
- **只增不改**：工具层没有 update / delete，历史本身就是价值。
- **有界注入**：`metrics` 只保留前三个键；`injectable=false` 标记"进日志但不进简报"的进度事件。

## 怎么接

```sh
# stdio MCP server，不需要任何第三方包
python3 mcp_server.py
```

存储：`~/Documents/ShadowRoom/_steward/ledger.db`（`SHADOWROOM_STEWARD_DB` 可覆盖）。
想重置直接删这个文件，没有别的地方引用它。

## 开发

```sh
python3 -c "import ast,sys; ast.parse(open('mcp_server.py').read())"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp_server.py
```
