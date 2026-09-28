# agent-lens

Observability and cost analytics for coding agents. It ingests Codex session logs
from your machine, turns them into structured token, cost and efficiency metrics in
a local database, mirrors the trace hierarchy to Langfuse, and renders everything in
a Chinese-first web dashboard.

> Status: design stage. See the design document below.

## 中文说明

agent-lens 是一个面向 coding agent 的观测与成本分析平台。它采集本机 Codex 的会话日志，
解析为结构化的 token 用量、成本与效率指标，落入本地 SQLite，同步到 Langfuse 做追踪分析，
并通过一个中文 Web 前端呈现。

它回答四个问题：

1. 我的 agent 花了多少 token、多少钱，落在哪个项目上？
2. 这些 token 花在什么地方（固定指令 / skill 目录 / 历史对话 / 工具输出）？
3. 传输与执行效率如何（cache 命中率、上下文膨胀、工具失败率、TTFT/TBT）？
4. 这些消耗换来了什么产出？

### 设计要点

- **文件是事实源，数据库是投影，Langfuse 是可重建的视图。** Codex 的原始 JSONL 永久保留，本地 SQLite 承担查询，Langfuse 只做分析层，过期后可随时重放重建。
- **采集可重放且幂等。** 以 `(文件路径, ordinal)` 为幂等键，进程重启从水位续读。
- **成本不落库。** 只存 token，成本在查询时按价目表计算，因此改价可整体重算，也支持试算。
- **入库前强制脱敏。** 密钥形态在写库前替换，原始文件不动。

### 技术栈

Python 3.12 + uv + FastAPI + SQLite + Langfuse SDK；Vue 3 + Vite + TypeScript + Element Plus + ECharts。
不依赖 Docker，Windows 与 macOS 均可运行。

### 文档

- 设计文档：[docs/superpowers/specs/2026-09-24-agent-lens-design.md](docs/superpowers/specs/2026-09-24-agent-lens-design.md)

### 怎么跑

```bash
uv sync                            # 安装依赖
uv run pytest                      # 跑测试
uv run agent-lens collect --once   # 扫描一次 ~/.codex/sessions 并入库
uv run agent-lens collect          # 常驻采集（默认 2 秒轮询，Ctrl-C 优雅退出）
uv run agent-lens status           # 看各表行数与上报队列状态
uv run agent-lens backfill         # 忽略水位，重扫全部历史会话
uv run agent-lens serve            # 查询 API + 前端，http://127.0.0.1:8000
```

默认数据库在 `~/.agent-lens/agent-lens.db`，默认扫描 `~/.codex/sessions`，
配置写在 `~/.agent-lens/config.toml`（TOML，字段见 `src/agent_lens/config.py`）。
开启 Langfuse 上报需要另装 SDK（`uv add langfuse`）并在配置里填 `[langfuse]` 段。

前端的开发与构建见 [web/README.md](web/README.md)：`npm run dev` 起 Vite 并把 `/api`
代理到 8000，`npm run build` 的产物由 `agent-lens serve` 同源托管。

### 阶段

1. 端到端最小闭环：采集器 + SQLite + Langfuse 上报 + 五个前端页面
2. 分析深化：上下文成本分解、skill 命中、模型与推理强度对比
3. 传输效率与数据生命周期：OTLP 指标接收器、归档与保留策略

### License

MIT
