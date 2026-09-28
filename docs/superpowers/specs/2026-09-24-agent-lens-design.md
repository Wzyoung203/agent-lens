# agent-lens 设计文档

- 日期：2026-09-24
- 状态：待评审
- 作者：王子扬
- 用途：个人项目，求职展示，边做边学

## 1. 背景与目标

agent-lens 是一个面向 coding agent 的观测与成本分析平台。它采集 Codex 在本机产生的会话数据，解析为结构化的 token 用量、成本与效率指标，落入本地数据库，同步到 Langfuse 做追踪分析，并通过一个中文 Web 前端呈现。

要回答的四个问题：

1. 我的 agent 一共花了多少 token、多少钱，分别落在哪个项目上？
2. 这些 token 花在什么地方（固定指令 / skill 目录 / 历史对话 / 工具输出）？
3. 传输与执行效率如何（cache 命中率、上下文膨胀、工具调用耗时与失败率、TTFT/TBT）？
4. 这些消耗换来了什么产出（改了多少文件、多少轮被中断、多少命令白跑）？

## 2. 设计原则

- **文件是事实源，数据库是投影，Langfuse 是可重建的视图。** 任何一层都可以从上一层重放得到。
- **一切统计可重放且幂等。** 采集器可以随时全量重跑而不产生重复数据。
- **先跑通端到端，再逐维度加深。** 每个阶段结束时都有一个可演示的完整产物。
- **不重复造 Langfuse 的轮子。** 自研前端只做 Langfuse 做不了的事：按项目的任意切片、上下文成本分解、工具失败折算成本、中文呈现。

## 3. 范围

### 3.1 采集范围

当前只采集本机 Codex（CLI / App）的会话数据。采集层按 adapter 接口设计，Codex 是第一个实现，未来接入第二个数据源（例如自写的 Python / LangChain agent）应是新增适配器，而不是重构。

### 3.2 阶段划分

**阶段 1 · 端到端最小闭环**
采集器 + SQLite 索引 + Langfuse 上报 + Web 前端五个页面（总览、项目视图、会话详情、工具调用、设置）。

**阶段 2 · 分析深化**
上下文成本分解、skill 命中明细、按模型与推理强度的成本对比。三者共用同一个上下文解析器。

**阶段 3 · 传输效率与数据生命周期**
OTLP 指标接收器（TTFT / TBT / API overhead）、归档与保留策略。

### 3.3 非目标

- **重复劳动检测**（相似失败聚类、同一文件反复读）——算法不成熟，不做。
- **多 agent 协作分析**——当前用量中未出现，不做。
- **预算告警与通知**——推迟到阶段 3 之后再评估。
- **本地自建 Langfuse**——已决定使用 Langfuse Cloud 免费版。
- **Langfuse 的 UI 复刻**——不做，自研前端与 Langfuse 界面并存，各司其职。

## 4. 关键约束（附实测依据）

以下结论来自对本机 8 个真实 Codex 会话文件（共 10.28 MB、3544 行、321 条 token 记录）的实测，不是推断。

### 4.1 数据源位置与结构

- 会话文件：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`，每行一个 JSON 对象
- 行类型：`session_meta`、`turn_context`、`response_item`、`event_msg`、`token_usage_record`、`world_state`
- 会话标题索引：`~/.codex/session_index.jsonl`（含中文 `thread_name`）
- 用户输入历史：`~/.codex/history.jsonl`

### 4.2 三层累计字段的陷阱

`token_usage_record` 的 payload 同时含三个对象：

- `usage`：**单次 API 调用**的用量
- `turn_token_usage`：本轮的累计用量
- `thread_token_usage`：本会话的累计用量

实测结论：**`thread_token_usage` 在每个文件内会重置。** 会话 `01a0c8ec` 横跨两个文件，文件一最后累计 4,209,407，文件二却从 21,052 重新开始。

因此确定两条硬性规则：

1. **任何总量统计一律对 `usage` 求和**，绝不读取累计字段作为结果。
2. 累计字段仅用于自检。校验公式：`Σ usage.input_tokens` 应等于各文件内最后一个 `thread_token_usage.input_tokens` 之和。实测该公式在全部会话上成立，例如 `01a0c8ec`：4,209,407 + 90,729 = 4,300,136 = Σ usage。

### 4.3 一个会话会跨多个文件

会话 `01a075a2` 的文件分别出现在 09-06 和 09-12 两天。会话与文件是一对多关系，不能用文件路径当作会话 ID。

### 4.4 Token 字段的包含关系

在 321 条记录上验证，零例外：

- `cached_input_tokens ⊆ input_tokens`
- `reasoning_output_tokens ⊆ output_tokens`

推论：**`output_tokens` 与 `reasoning_output_tokens` 不可相加**，那是重复计费。

### 4.5 数据源在读取时仍在增长

本文档撰写过程中，当前会话的 token 记录从 317 条增长到 321 条。采集器必须处理「文件正在被追加写入」的常态。

### 4.6 Schema 会随版本漂移

09-03 的会话在 Codex CLI `0.153.4` 下产生，**完全没有 `token_usage_record`**；当前版本为 `0.154.0`。解析器必须容忍字段缺失，并记录每份文件的 `cli_version`。

### 4.7 超长行

最长单行 273,823 字符（`session_meta.base_instructions`）。体积高度集中：前 1% 的行占总量的 22.9%，前 10% 占 62.4%，中位行长 864 字符。解析器必须支持超长行（PowerShell 的 `ConvertFrom-Json` 会在此类行上失败，Python 无此限制）。

### 4.8 体积与压缩

- 单会话文件 0.05–2.95 MB，中位数约 1.3 MB
- gzip 压缩率实测 14.1%（2.95 MB 压缩到 0.42 MB）
- 按当前使用强度估算约 170 MB/年原始数据

### 4.9 Langfuse Cloud 免费版限制

- 每月 50,000 units
- **数据保留 30 天**
- 上传速率上限 1,000 请求/分钟

30 天保留决定了第 7 节的设计；1,000 请求/分钟的上限决定了上报必须批量加限速（见第 10 节）；50,000 units/月决定了上报粒度不能是「一条原始记录一条观测」。

### 4.10 Codex 内置 OTLP 导出能力

Codex 可执行文件内含完整 OTLP 导出实现。配置段为 `[otel]`，键包括 `environment`、`exporter`（取值含 `otlp-http` / `otlp-grpc`）、`trace_exporter`、`metrics_exporter`、`log_user_prompt`、`span_attributes`；每种 exporter 支持 `endpoint`、`headers`、`protocol`、`tls`。

可用的延迟类指标名包括 `codex.responses_api_engine_iapi_ttft.duration_ms`、`codex.responses_api_engine_iapi_tbt.duration_ms`、`codex.responses_api_overhead.duration_ms`、`codex.api_request.duration_ms`。

这些指标**不在** JSONL 文件中，只能通过 OTLP 获取，因此归属阶段 3。

### 4.11 token_usage_record 不带时间戳（2026-09-28 核实）

`token_usage_record` 的 payload 只有 `response_id` / `root_turn_id` / `session_id` /
`thread_id` / `turn_id` 与三组 usage，**没有时间戳字段**（在 2026-09-27 的真实会话文件上
逐条核实）。因此 `api_calls.timestamp` 在真实数据上一律为 NULL，解析层给不出单次调用的
绝对时刻。

可用的时间锚点只有以下三类：

- `session_meta.timestamp`：会话创建时刻
- `task_started.started_at` / `task_complete.completed_at`（秒级 epoch）：turn 起止，
  已落进 `turns.started_at` / `turns.completed_at`
- `item_completed.started_at_ms` / `completed_at_ms`（毫秒级 epoch）：单条 item 的起止

对 P1.4 的影响与决定：日趋势分桶与时段计价都需要「调用发生时刻」，而单次调用没有，
因此 schema v3 给 `api_call_view` 增加派生列
`occurred_at = COALESCE(a.timestamp, t.started_at, s.recorded_at, s.first_seen_at)`，
把时间粒度降到 turn 级。这与 6.5 阶段 1 的指标口径一致（轮次耗时、上下文膨胀本就是
turn 级）；若将来需要调用级时刻，只能靠阶段 3 的 OTLP。

## 5. 系统架构

```
~/.codex/sessions/**.jsonl          事实源（Codex 写入，永久保留）
        | 文件变更监听
        v
(1) Collector   增量解析 -> 脱敏 -> 幂等键去重 -> 写入 SQLite
        |                                      -> 投入上报队列
        v
(2) Reporter    批量取队列 -> langfuse SDK -> 失败退避重试 -> 记录上报水位
        |
        v
   Langfuse Cloud（视图层，30 天后过期，可从 (1)(2) 重建）

(3) API (FastAPI)   只读 SQLite，对外提供聚合查询
        v
(4) Web (Vue 3)     中文仪表盘
```

### 5.1 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| Collector | 监听文件、解析、脱敏、写库、入队 | 网络上报 |
| Reporter | 批量上报、重试、水位记录 | 解析与聚合 |
| API | 聚合查询、触发回填、管理配置 | 写业务数据 |
| Web | 展示与配置 | 直连 Langfuse |

Reporter 在阶段 1 与 Collector 同进程（协程），但接口上保持独立，便于后续拆分。

### 5.2 幂等与断点续传

- **幂等键**：`(文件路径, ordinal)`。`ordinal` 是每一行自带的、文件内唯一的序号，跨文件也唯一。
- **水位表**：记录每个文件的 `last_ordinal`、读取偏移、文件大小与 mtime。进程重启后从水位续读。
- **去重**：所有写入使用 `INSERT OR IGNORE`（以幂等键为唯一约束），上报前检查该记录的上报状态。
- **回填**：提供手动触发接口，可按时间范围或会话重放。因为事实源永久保留，回填是任意时刻可用的能力。

### 5.3 文件监听策略

采用「周期性扫描 + 增量读取」，轮询间隔 1 到 2 秒，而非纯事件驱动。理由：事件驱动在 Windows 与 macOS 上行为不一致，且无法覆盖进程未运行期间产生的文件。扫描时通过对比文件大小与 mtime 判断是否有新内容，配合水位表只读取新增部分。

读取时必须处理半行：文件末尾可能是不完整的 JSON 行，解析失败时保留该行内容并后退偏移，等待下次读取补齐。

### 5.4 现成轮子与自研部分的边界

明确哪些能力由现成组件承担、哪些必须自研，避免后续计划重复造轮子或误判工作量。

| 能力 | 由谁提供 |
|---|---|
| 批量发送、失败重试、退出时 flush | langfuse Python SDK v3（基于 OpenTelemetry，后台导出） |
| 接入协议 | Langfuse 的 OTLP 端点，标准协议 |
| trace 树、会话视图、过滤搜索、成本图表、公开分享链接 | Langfuse 服务端 |
| 人工标注、eval、数据集导出 | Langfuse 内置 |
| 自建部署模板 | 官方 docker-compose / Helm chart |
| **JSONL 到标准 trace / span / generation 的解析与映射** | **必须自研** |
| **跨文件会话、累计字段重置、半行 JSON、schema 漂移的处理** | **必须自研** |
| **进程被杀时的落盘队列与重启重投** | **必须自研**（SDK 的批量在内存中，进程死亡会丢数据） |
| **聚合分析、项目切片、上下文成本分解、中文界面** | **必须自研** |

结论：Langfuse 省掉的是外围苦工（上报、存储、通用展示），不改变项目的核心工作量。把本机日志重建成标准模型这一层没有现成适配器，而它正是本项目真正的技术含量所在。

补充一个待验证机会：Codex 自身可导出 OTLP、Langfuse 又有 OTLP 端点，阶段 3 的延迟指标可能直连即可，无需自建接收器。是否成立见第 13 节第 3 条。

### 5.5 上报粒度与配额兜底

Langfuse 免费版每月 50,000 units，因此上报粒度做成可配置项，而非写死在代码里：

```yaml
langfuse:
  granularity: full      # full | turn
  batch_size: 50
```

- `full`（默认，已决定）：每个 api_call 与 tool_call 各占一个观测。信息最全，单位消耗最高。
- `turn`（配额兜底）：一轮上报一个 trace，附一个 generation 汇总与关键 span。单位消耗显著降低，仍足以支撑标准视图与演示。

降级只是配置变更，不需要改架构。若配额仍然吃紧，下一步是关闭 Langfuse sink——Reporter 与采集层解耦，关掉不影响本地分析与历史数据。

## 6. 数据模型与指标口径

### 6.1 层级

```
project（项目）
└── session（会话，来自 Codex session_id）
    └── turn（轮次，来自 turn_id）
        └── api_call（一次 LLM 请求，来自 response_id）
            └── tool_call（一次工具调用，来自 call_id）
```

### 6.2 Langfuse 映射

| 本系统 | Langfuse 实体 | 说明 |
|---|---|---|
| session | session_id | 直接透传 |
| turn | trace | 一轮一个 trace |
| api_call | generation | 带 token 用量与模型 |
| tool_call | span | 带耗时与成功状态 |

按 turn 而非按 session 建 trace：一个会话可持续数小时、数十轮（实测最长 79 次 API 调用），按会话建 trace 会得到无法阅读的巨型树，且成本无法归因到具体轮次。

### 6.3 核心表

- `sessions`：会话元信息、cwd、cli_version、项目归属、标题
- `turns`：轮次边界、起止时间、耗时、结束原因（正常 / 中断 / 出错）、模型、推理强度
- `api_calls`：幂等键、时间戳、模型、六项 token 字段、缓存命中率、所属 turn
- `tool_calls`：工具名、起止时间、耗时、成功状态、退出码、结果摘要
- `items`：`item_completed` 事件（类型、起止毫秒），用于耗时计算
- `events`：压缩、中断、文件修改等离散事件
- `pricing`：provider、model、三档价格、生效时间
- `projects`：项目名、路径规则、归属映射
- `ingest_state`：文件水位与上报状态

所有表以幂等键为唯一约束。正文不入库，只保留「原始文件 + 行号」指针。

### 6.4 成本计算

成本**不落库**，只在查询时计算：

```
cost = (input_tokens - cached_input_tokens) * cache未命中价
     + cached_input_tokens * cache命中价
     + output_tokens * output价
```

理由有三：

1. 修改价目表后历史趋势一致重算，不会出现断层；
2. 支持试算（「如果单价涨 30%，上月多花多少」）；
3. 符合事实表 + 维度表的标准建模，成本是可推导属性而非事实。

`pricing` 表每条记录带 `effective_from`，因此也能做历史精确计价。前端可切换「按当前价目表重算」或「按当时价目表计价」。

价目表在设置页可增删改：provider 到 model 到三档价格。Codex 的 provider 是 deepseek，Langfuse 内置价格表不适用，本地价目表是唯一来源。

**定价数据（2026-09-27 核实，供 P1.4 设置页使用）**

deepseek 官方按「高峰 / 空闲」两档计价，空闲价一律是高峰价的一半。每百万 token 的美元价：

| 模型 | 输入（缓存命中） | 输入（缓存未命中） | 输出 |
|---|---|---|---|
| deepseek-flash | 0.003 / 0.006 | 0.15 / 0.3 | 0.6 / 1.2 |
| deepseek-v4-pro | 0.022 / 0.044 | 0.66 / 1.32 | 1.98 / 3.96 |

（单元格为「空闲 / 高峰」。对应的人民币价目表是 0.02 / 0.04、1 / 2、4 / 8 元。）高峰时段为
UTC 01:00–04:00 与 06:00–10:00 的周一至周五，排除中国法定节假日；其余时间全部按空闲价，
含周末与节假日全天。

对 P1.4 的两条约束：

1. `pricing` 表当前只有 `(provider, model, effective_from)` 三个维度，**表达不了时段**。设置页落地前
   必须先决定时段维度的建模方式（例如增加 `time_window` 列、按时段查价；节假日日历可选）。
2. `deepseek-v4-flash` 是官方遗留模型名，实际按 Flash 价计价。录入历史价目表与匹配历史日志时需要做
   模型名归一化，否则这些调用会落到「查不到价目表」。

### 6.5 传输效率指标

| 指标 | 定义 | 数据来源 | 阶段 |
|---|---|---|---|
| cache 命中率 | `cached_input_tokens / input_tokens` | JSONL | 1 |
| 上下文膨胀率 | 本轮 input 除以上一轮 input | JSONL | 1 |
| 工具调用耗时 | `item_completed` 的毫秒差，或输出中的 `Wall time` | JSONL | 1 |
| 工具失败率 | 退出码非 0、`patch_apply_end.success = false` | JSONL | 1 |
| 轮次耗时 | `task_complete.duration_ms` | JSONL | 1 |
| 中断率 | `turn_aborted` 事件占比 | JSONL | 1 |
| TTFT | 首字节延迟 | 仅 OTLP | 3 |
| TBT | token 间隔 | 仅 OTLP | 3 |

阶段 1 能覆盖八项中的六项，延迟类两项必须等阶段 3。

### 6.6 工具失败折算成 token 成本

工具调用失败会触发重试，而重试那次调用的 input 通常是数万 token（实测单次调用 input 在 18,974 至 106,262 之间）。

因此定义一个派生指标：**失败成本 = 该 turn 内因工具失败而产生的额外 api_call 的 input token 乘对应单价**。这是自研前端独有的视角，Langfuse 无法提供。

### 6.7 上下文成本分解（阶段 2）

每次 API 调用的 input 拆成四块：

1. 固定指令：`base_instructions`，实测 17,766 字符，每个会话固定，每次调用重新发送
2. skill 目录与协作模式说明：developer 消息，实测每会话 16,833 到 73,446 字符
3. 历史对话：消息与推理内容
4. 工具输出：`function_call_output` 与 `custom_tool_call_output` 的正文

必须与 cache 命中率联合呈现：固定指令部分的 token 数字很大，但有 prompt cache 时实际成本远低于数字本身（实测某次调用 input 24,207，其中 19,200 命中缓存）。

### 6.8 Skill 命中（阶段 2）

Skill 加载表现为一次工具调用，其参数指向 `SKILL.md` 路径。实测 329 次工具调用中有 29 次命中该模式。

呈现内容：skill 名、被加载次数、其说明文本的 token 成本、加载后该轮的成本变化。

### 6.9 模型与推理强度对比（阶段 2）

`turn_context` 逐轮记录 `model` 与 `effort` 字段，`thread_settings_applied` 记录 `reasoning_effort`。因此可以对比同一项目在不同推理强度下的成本，以及不同模型间的单位成本差异。

## 7. 数据保留与归档

三层数据的增长特性完全不同：

| 层 | 增长 | 策略 |
|---|---|---|
| SQLite 指标 | 极慢（321 次调用仅数百 KB） | 永久保留，不清理 |
| Langfuse | 由服务端管理 | 30 天自动过期，无需干预 |
| 原始 JSONL | 唯一会膨胀的一层 | 超期压缩归档 |

配置项：

```yaml
retention:
  metrics_keep: forever
  raw_keep_days: 14
  archive_after_days: 14
  archive_dir: ~/.codex-archive
  delete_raw_after_archive: false
```

三条约束：

1. **归档前必须确认该时间段的指标已入 SQLite。** 这保证压缩只丢失正文，指标无损、统计不断档。
2. **归档格式为 gzip**，采集器直读 `.gz`，实测压缩率 14.1%。
3. **默认只归档不删除。** `~/.codex/sessions` 属于 Codex，删除可能影响其自身会话历史功能。`delete_raw_after_archive` 默认 `false`，开启前需先验证 Codex 对旧文件的依赖程度。

## 8. 安全与脱敏

原始日志会包含大量敏感内容：命令回显、文件内容、配置片段、环境变量，乃至密钥。

本项目在开发过程中已真实发生一次：检查 `~/.git-credentials` 时把 GitHub token 打印进了会话，该 token 随即被写入 `~/.codex/sessions/` 下的 JSONL，也就是本项目的采集对象。

因此脱敏是**写入 SQLite 之前**的一道强制处理，不是可选功能：

- 匹配并替换常见密钥形态：`ghp_`、`github_pat_`、`sk-`、`AKIA`、JWT、Bearer token、`password=` / `token=` / `key=` 赋值
- 命中即替换为 `[REDACTED:<类型>]`，原始文件保持不动（不修改 Codex 的数据）
- 脱敏作用于入库正文摘要与上报到 Langfuse 的字段
- 脱敏规则可配置，并提供 dry-run 模式查看命中情况

## 9. 前端设计

### 9.1 页面

| 页面 | 内容 | 阶段 |
|---|---|---|
| 总览 | 本月花费 / token 总量 / cache 命中率 / 会话数四张卡片；按天 token 与成本趋势；项目消费排行 | 1 |
| 项目视图 | 左栏项目列表，右侧该项目趋势、会话列表、成本构成 | 1 |
| 会话详情 | 时间线上每个 turn 一行（token、成本、cache 命中率、工具调用数、耗时），可展开至每次 api_call | 1 |
| 工具调用 | 工具类型分布与排名、各工具平均与 P95 耗时及失败率、失败清单、单轮瀑布图、跨项目失败率对比、失败折算成本 | 1 |
| 设置 | 价目表编辑、项目映射编辑、保留策略、Langfuse 连接状态 | 1 |
| 上下文成本 | 四块成本构成、skill 命中明细、模型与推理强度对比 | 2 |
| 传输效率 | TTFT / TBT 分布、cache 命中率趋势、上下文膨胀曲线 | 3 |

### 9.2 项目归属

三种规则，按优先级：

1. **手动映射**：配置中以路径前缀匹配项目名。优先级最高。
2. **自动推断**：从 `cwd` 向上查找 `.git` 目录，仓库根作为项目边界。因此 `D:\Study\6000c\starter\maie6000c-starter-wzy` 及其子目录归为同一项目。
3. **未归类兜底**：无法判断的 `cwd` 进入「未归类」分组，可在设置页一键指派。

手动映射是必需项而非可选项：`D:\codex\wzy_workstudio` 与 `D:\Study\codex_projects` 这类「元目录」下会开多个不同项目的活，仅靠仓库根推断会把它们混为一谈。

### 9.3 技术选型

- 前端：Vue 3 + Vite + TypeScript + Element Plus（中文 locale）+ ECharts
- 后端：Python 3.12 + uv（依赖管理）+ FastAPI + pydantic
- 存储：SQLite（单文件，无服务）
- 上报：langfuse Python SDK（已确认支持 `session_id`、`metadata`、`trace_id`）
- 文件监听：不使用 watchdog，改为自实现轮询扫描（见 5.3）

选择 Vue 而非 React：作者已有 Vue 3 经验，且中文生态（Element Plus、ECharts 中文文档）是硬需求。选择 Python 而非 Spring Boot：LLM 观测领域的现成轮子集中在 Python，Java 在「长驻进程 + 增量读文件 + 批量上报」这类活上会明显拖慢迭代。

不依赖 Docker。`uv sync` 与 `npm install` 即可在 Windows 与 macOS 上运行。

### 9.4 实时性

阶段 1 用 10 秒轮询。阶段 3 若确有实时需求再引入 SSE。

### 9.5 语言

界面中文优先。README 采用双语（英文在前，中文在后），便于公开仓库被非中文读者理解。

## 10. 错误处理

| 场景 | 处理方式 |
|---|---|
| 文件正在写入，读到半行 JSON | 保留该行、回退偏移，下次读取补齐 |
| 单个会话文件解析失败 | 隔离该文件并记录错误，不阻塞其他文件；水位不推进 |
| Schema 漂移（字段缺失或改名） | 所有字段按可选处理；记录 `cli_version`；失败行计入统计并在设置页展示 |
| Langfuse 不可用或触发限流 | 重试与退避由 SDK 的 OTLP 导出器承担；我们负责把未发送的数据落盘成持久队列并在重启后重投；采集端全程不受影响 |
| 达到 1,000 请求/分钟上限 | Reporter 主动限速，按批发送，批大小可配置 |
| 进程异常退出 | 从 `ingest_state` 水位恢复；未完成的上报重新入队 |
| SQLite 并发访问 | 单写者模式，开启 WAL；写操作集中在 Collector 进程 |
| 幂等键冲突 | `INSERT OR IGNORE`，视为已处理 |
| 原始文件被归档或删除 | 标记为「源已归档」；指标数据保留；回填前需先解压 |
| 时区与时间戳单位混用 | 统一存 UTC；源数据中存在秒级与毫秒级两种时间戳，解析时归一化 |

## 11. 测试策略

分层，从便宜到贵：

1. **解析器单元测试**：用真实数据的裁剪样本（去除敏感内容）构造 fixture，覆盖三类边界——无 token 记录的旧版本会话、跨文件会话、超长行。核心断言：`Σ usage` 等于各文件最后一个 `thread` 累计之和。
2. **幂等性测试**：同一份 fixture 连续处理两次，断言记录数与成本总量不变。
3. **脱敏测试**：构造含各类密钥形态的样本，断言入库内容已替换且原文未改动。
4. **成本计算测试**：断言包含关系（`cached ⊆ input`、`reasoning ⊆ output`）与公式结果，覆盖改价重算与历史计价两条路径。
5. **API 集成测试**：FastAPI 聚合查询接口，构造小型数据库断言返回结构。
6. **端到端冒烟测试**：以临时目录模拟 `~/.codex/sessions`，跑通采集到查询的完整链路，Reporter 以假实现替换，不依赖 Langfuse。

阶段 1 不引入真实 Langfuse 的自动化测试，改为手动验证一次上报链路。

## 12. 交付方式

每个阶段拆成 3 到 4 个相互独立、可分别执行的实现计划，逐个完成并验证，不做大爆炸式交付。

每个计划的拆分粒度与验收标准在写计划阶段确定。本文档只固定阶段边界（见 3.2）。

## 13. 待验证问题

以下问题在对应阶段开始前必须先验证，不允许凭推断设计：

1. **JSONL 时间戳粒度是否足以计算延迟类派生指标。** 若不足，TTFT / TBT 完全依赖阶段 3 的 OTLP 接收器。验证方式：用真实数据比对 `task_started.started_at` 与 `token_count` 事件的间隔。
2. **deepseek 对 reasoning token 是否单独计价。** 从数据结构无法判断，需核对账单。若单独计价，`pricing` 表需增加第四档价格。
3. **Langfuse 的 OTLP 端口是否接收 metrics 类型。** 若不接收，阶段 3 的延迟指标需由自建 OTLP 接收器直接写入 SQLite。验证方式：查阅 Langfuse 文档并向其端点发送测试数据。
4. **Codex 对旧会话文件的依赖程度。** 决定是否开放「归档后删除原始文件」选项。验证方式：把一份旧文件移走，观察 Codex 的会话列表与恢复功能是否受影响。
5. **Langfuse 免费版 unit 的精确计量口径。** 决定上报粒度。验证方式：查阅官方定价说明，并用少量数据实测消耗。

## 14. 已知风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| Codex 升级后日志格式变化 | 解析失效 | 记录 `cli_version`；解析器字段全部可选；失败行可见 |
| 免费版 30 天保留导致云上数据丢失 | 无法查历史 | Langfuse 定位为可重建视图，本地 SQLite 才是查询层 |
| 采集器常驻占用资源 | 影响 agent 使用体验 | 只读文件、轮询间隔 1 到 2 秒、解析在独立进程 |
| 范围蔓延（观测维度持续增加） | 项目做不完 | 阶段划分与 3.3 非目标清单；新维度先归入后续阶段 |
| 上游凭据泄露 | 安全问题 | 入库前强制脱敏（第 8 节） |
