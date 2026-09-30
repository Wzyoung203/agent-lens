# P3.1 传输效率指标落地（TTFT / 轮次时长 / TBT 估算） 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「首 token 延迟（TTFT）、轮次时长、token 间延迟（TBT 估算）」从事实源落地到库里，并给出可查询的分布接口——这是 P3.2 前端页的数据前置。

**Architecture:** 不加外部依赖、**不建 OTLP 接收器**：真实日志的 `task_complete` 事件里已经带了 `time_to_first_token_ms` 与 `duration_ms`（见下「先期验证」），所以这一层是既有的 parser → storage → queries → API 链路往前的自然延伸，schema 升到 v6。

**Tech Stack:** Python 3.12、SQLite、FastAPI、pydantic、pytest、uv。

**Spec:** 设计文档 9.1「传输效率」页、阶段 3 的 P3.1/P3.2

**Depends on:** P1.1 解析器、P1.2 存储层、P1.4a 查询/接口层

## Global Constraints

- 事实源只读；正文不入库（`last_agent_message` 等长文本一律不存）。
- 时间统一 UTC aware；分桶一律用 `turns.started_at`。
- TBT 是**派生估算**：`(duration_ms - time_to_first_token_ms) / output_tokens`，必须标注为估算口径（它不是真实逐 token 间隔），不得当成事实字段。
- schema 迁移可重复执行，旧库（v5）原地升到 v6，不要求重放。

## 先期验证（2026-09-30，真实 `~/.codex/sessions`）

扫 36 个文件、7601 条 `response_item` 与 7452 条 `event_msg`：

| 字段 | 载体 | 出现次数 |
|---|---|---|
| `time_to_first_token_ms` | `event_msg.task_complete` | 173 |
| `duration_ms` | `event_msg.task_complete` / `turn_aborted` | 185 |
| `started_at` / `completed_at` | `event_msg.task_complete` | 173 |

**结论**：设计文档 13.1 第 1 条（2026-09-28）**已经记过**这条实测——TTFT 在 JSONL 里，不必等阶段 3；
本轮是把它落地实现，并顺手修正两处口径：

1. 阶段 3 表格里「TTFT：仅 OTLP」与 13.1 自相矛盾，已改成「JSONL 的 `task_complete` 字段」。
2. 13.1 里「TBT 只能靠 OTLP」也偏严：用 `(duration_ms - ttft) / output_tokens` 能给出**估算值**
   （实测 ≈9.9ms/token）。真实逐 token 间隔与 API overhead 仍然只有 OTLP 能给，所以 OTLP 接收器
   从「P3.1 的前置」降级为「将来要精确延迟时的可选项」。

---

## Task 1: 解析 + 落库（schema v6）

**Files:**
- Modify: `src/agent_lens/models.py`（`TurnRecord.time_to_first_token_ms`）
- Modify: `src/agent_lens/parser.py`（`task_complete` 带上该字段）
- Modify: `src/agent_lens/schema.sql`（`turns` 加列）
- Modify: `src/agent_lens/storage.py`（`SCHEMA_VERSION = 6`、迁移、UPSERT）
- Test: `tests/test_storage_latency.py`、`tests/test_schema_latency.py`

- [ ] **Step 1:** 写失败测试：解析 `task_complete` 得到 ttft；写库后 `SELECT time_to_first_token_ms FROM turns` 等于原值；v5 旧库 `init_db` 后自动补列且不丢数据。
- [ ] **Step 2:** 跑测试确认失败。
- [ ] **Step 3:** 实现：`TurnRecord` 加字段；`parser` 的 `task_complete` 分支补 `time_to_first_token_ms=payload.get("time_to_first_token_ms")`；`schema.sql` 的 `turns` 加 `time_to_first_token_ms INTEGER`；`SCHEMA_VERSION = 6`；`migrate()` 里 `ALTER TABLE turns ADD COLUMN`（先判断列是否已存在）；`_upsert_turns` 的 INSERT 与 `DO UPDATE` 都带 `COALESCE(...)`。
- [ ] **Step 4:** 跑测试确认通过（含全量回归）。
- [ ] **Step 5:** 提交 `feat: store time to first token from task_complete events`。

---

## Task 2: 查询 + 接口

**Files:**
- Modify: `src/agent_lens/queries.py`、`src/agent_lens/api/app.py`、`src/agent_lens/api/schemas.py`
- Add: `src/agent_lens/api/routes/latency.py`
- Test: `tests/test_queries_latency.py`

**Interfaces:**

- `queries.LatencyStat(samples: int, ttft_avg_ms: float, ttft_p50_ms: float, ttft_p90_ms: float, ttft_p95_ms: float, turn_avg_ms: float, turn_p90_ms: float, tbt_avg_ms: float)`
- `queries.LatencyPoint(day: str, samples: int, ttft_avg_ms: float, ttft_p90_ms: float)`
- `queries.LatencyResponse(range: RangeInfo, overall: LatencyStat, daily: list[LatencyPoint])`
- `queries.latency_stats(conn, *, days=30, project=None, now=None) -> LatencyResponse`
- `GET /api/latency?days=30&project=...`

- [ ] **Step 1:** 写失败测试：造两个 turn（确定值），断言 p50/p90 与 TBT 估算口径；空库返回零值而不是报错。
- [ ] **Step 2:** 跑测试确认失败。
- [ ] **Step 3:** 实现查询与路由；`/api/latency` 注册进 `app.py`。
- [ ] **Step 4:** 跑测试 + ruff。
- [ ] **Step 5:** 提交 `feat: expose latency statistics over the api`。

---

## Task 3: 真实库重放 + 文档

- [ ] **Step 1:** 用真实会话重放进临时库，确认 TTFT 样本数与「先期验证」的 173 条量级一致、且 p50/p90 是合理毫秒数（不是 0 或几十万）。
- [ ] **Step 2:** 设计文档 9.1 与阶段 3 更新：P3.1 改为「从日志落地延迟指标」，写明 OTLP 接收器不再是必需项及理由；`plans/README.md` 同步。
- [ ] **Step 3:** 提交。

---

## Self-Review

- **推翻的是假设，不是目标**：TTFT / TBT 仍然要落地，只是不需要 OTLP 这条管道。
- **口径诚实**：TBT 标为派生估算；TTFT 是日志里的原始字段。
- **下一步**：P3.2 前端页（延迟分布、cache 趋势、上下文膨胀曲线）在 Task 2 的接口之上做。
