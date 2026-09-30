# P2.3 模型与推理强度成本对比 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `(模型, 推理强度)` 分组对比调用量、token 结构、cache 命中率与成本，回答「同一件事用不同模型 / 不同 effort 差多少钱」。

**Architecture:** 只做查询 + API + 前端，**不改 schema**——`turns.model` / `turns.effort` 在 P1.3 就已落库，`api_call_view` 也已带出这两列。成本一律走既有的 `queries._CostedRows`（查询时按价目表算，改价后历史一致重算）。

**Tech Stack:** Python 3.12、SQLite、FastAPI、pytest、Vue 3 + Element Plus + ECharts、uv。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（6.9 模型与推理强度对比、6.4 成本不落库、9.1 上下文成本页）

**Depends on:** P2.1（复用 `web/src/views/ContextView.vue`）

## Global Constraints

- 与 P2.1 / P2.2 相同：事实源只读、正文不入库、UTC aware、标识符英文 / 注释中文。
- **成本不落库**：本计划不得新增任何成本列，全部现算。
- `output_tokens` 已含 `reasoning_output_tokens`，展示时不得相加。
- 模型名先过 `pricing.normalize_model` 再匹配价目表（`deepseek-v4-flash` → `deepseek-flash`）。

## 已完成的先期验证

对真实 `~/.codex/sessions` 实测：`turn_context` 共 113 条，`model` 与 `effort` **两条字段都 100% 非空**——
`model` 分布 `deepseek-flash` 107 / `deepseek-v4-pro` 6，`effort` 分布 `low` 72 / `high` 41。
也就是说这次对比在真实数据上立刻有内容可看，不必造假数据。

---

## Task 1: 查询层 `model_comparison`

**Files:**
- Modify: `src/agent_lens/queries.py`
- Test: `tests/test_queries_models.py`

**Interfaces:**

- Consumes: `_range_bounds`、`_range_info`、`_fetch_api_rows`、`_CostedRows`、`pricing.normalize_model`
- Produces:
  - `queries.ModelEffortStat(model: str, effort: str, calls: int, turn_count: int, input_tokens: int, cached_input_tokens: int, output_tokens: int, cache_hit_rate: float, cost: float, avg_cost_per_call: float, avg_input_tokens: float, unpriced_calls: int, currency: str)`
  - `queries.ModelComparisonResponse(range: RangeInfo, rows: list[ModelEffortStat], currency: str, total_cost: float)`
  - `queries.model_comparison(conn, *, days=30, project=None, now=None) -> ModelComparisonResponse`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queries_models.py
import math

from agent_lens import queries


def test_model_comparison_groups_by_model_and_effort(seeded_db):
    result = queries.model_comparison(seeded_db, days=3650)
    keys = {(row.model, row.effort) for row in result.rows}
    assert keys  # 见下：本用例断言真实分组键与成本口径
    for row in result.rows:
        if row.input_tokens:
            assert math.isclose(
                row.cache_hit_rate,
                row.cached_input_tokens / row.input_tokens,
                rel_tol=1e-9,
            )
        assert row.calls >= 1
        assert row.avg_input_tokens >= 0
    assert math.isclose(sum(row.cost for row in result.rows), result.total_cost, rel_tol=1e-9)
```

> `seeded_db` 是本计划唯一需要新增的 fixture：本仓库现有测试没有现成的「带 turn 模型/强度的库」。
> 执行者在 `tests/conftest.py` 追加一个 fixture，用 `write_parsed_session` 造两条会话数据：
> 一条 `turn_context.model="deepseek-flash", effort="low"`、另一条 `"deepseek-v4-pro", effort="high"`，
> 每条各带 1 到 2 次 `token_usage_record`（`usage` 带 `input_tokens` / `cached_input_tokens` / `output_tokens`），
> 并用 `upsert_price` 写两档价目表（照 `tests/conftest.py` 里 `SEEDED_NOW` 的既有写法）。
> 断言再补一条：`{("deepseek-flash", "low"), ("deepseek-v4-pro", "high")} <= keys`。

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_queries_models.py -v`
Expected: FAIL，`AttributeError: module 'agent_lens.queries' has no attribute 'model_comparison'`

- [ ] **Step 3: Write minimal implementation**

```python
class ModelEffortStat(BaseModel):
    model: str
    effort: str
    calls: int
    turn_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cache_hit_rate: float
    cost: float
    avg_cost_per_call: float
    avg_input_tokens: float
    unpriced_calls: int
    currency: str


class ModelComparisonResponse(BaseModel):
    range: RangeInfo
    rows: list[ModelEffortStat]
    currency: str
    total_cost: float


def model_comparison(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    now: datetime | None = None,
) -> ModelComparisonResponse:
    """按 (模型, 推理强度) 对比成本。成本现算，不落库（设计文档 6.4）。"""
    start, end = _range_bounds(now, days)
    costed = _CostedRows(conn, now=now)
    rows = _fetch_api_rows(conn, start, end, project=project)

    buckets: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        model = normalize_model(row["model"]) or row["model"] or "未知模型"
        effort = row["effort"] or "未知强度"
        bucket = buckets.setdefault(
            (model, effort),
            {"calls": 0, "turns": set(), "input": 0, "cached": 0, "output": 0,
             "cost": 0.0, "unpriced": 0, "currency": costed.currency(row)},
        )
        bucket["calls"] += 1
        if row["session_id"] and row["turn_id"]:
            bucket["turns"].add((row["session_id"], row["turn_id"]))
        bucket["input"] += _row_tokens(row, "input_tokens")
        bucket["cached"] += _row_tokens(row, "cached_input_tokens")
        bucket["output"] += _row_tokens(row, "output_tokens")
        amount, priced = costed.cost(row)
        bucket["cost"] += amount
        if not priced:
            bucket["unpriced"] += 1

    stats: list[ModelEffortStat] = []
    for (model, effort), bucket in buckets.items():
        calls = int(bucket["calls"])
        input_tokens = int(bucket["input"])
        stats.append(
            ModelEffortStat(
                model=model,
                effort=effort,
                calls=calls,
                turn_count=len(bucket["turns"]),
                input_tokens=input_tokens,
                cached_input_tokens=int(bucket["cached"]),
                output_tokens=int(bucket["output"]),
                cache_hit_rate=_cache_hit_rate(int(bucket["cached"]), input_tokens),
                cost=float(bucket["cost"]),
                avg_cost_per_call=(float(bucket["cost"]) / calls) if calls else 0.0,
                avg_input_tokens=(input_tokens / calls) if calls else 0.0,
                unpriced_calls=int(bucket["unpriced"]),
                currency=str(bucket["currency"]),
            )
        )
    stats.sort(key=lambda item: item.cost, reverse=True)
    currency = stats[0].currency if stats else "USD"
    return ModelComparisonResponse(
        range=_range_info(start, end, days),
        rows=stats,
        currency=currency,
        total_cost=sum(item.cost for item in stats),
    )
```

导入补充：`from .pricing import normalize_model`（若 `queries.py` 已导入 pricing 的其他名字，合并到同一行）。

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_queries_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_lens/queries.py tests/test_queries_models.py tests/conftest.py
git commit -m "feat: compare cost across models and reasoning efforts"
```

---

## Task 2: `/api/models`

**Files:**
- Create: `src/agent_lens/api/routes/models.py`
- Modify: `src/agent_lens/api/app.py`、`src/agent_lens/api/schemas.py`
- Test: `tests/test_api_endpoints.py`（新增一条）

**Interfaces:**

- Produces: `GET /api/models?days=&project=` 返回 `ModelComparisonResponse`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_api_endpoints.py
def test_models_endpoint_returns_rows(api_client):
    response = api_client.get("/api/models?days=3650")
    assert response.status_code == 200
    body = response.json()
    assert "rows" in body and "total_cost" in body
```

> `api_client` 沿用该测试文件里既有的 client fixture 名字；如果该文件用的是别的写法（例如每次
> `TestClient(create_app(...))`），照该文件既有风格写，不要新造一套。

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_api_endpoints.py -k models -v`
Expected: FAIL，404（路由不存在）

- [ ] **Step 3: Write minimal implementation**

```python
# src/agent_lens/api/routes/models.py
"""模型与推理强度成本对比接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import ModelComparisonResponse

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", response_model=ModelComparisonResponse)
def models(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> ModelComparisonResponse:
    return queries.model_comparison(conn, days=days, project=project)
```

`schemas.py` 重导出 `ModelComparisonResponse` 与 `ModelEffortStat`；`app.py` 注册 `models` 路由
（注意与既有路由的导入名冲突，必要时用 `models_routes` 别名；`create_app` 里的路由元组同步加进去）。

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_api_endpoints.py -v`；再跑全量 + ruff。

- [ ] **Step 5: Commit**

```bash
git add src/agent_lens/api tests/test_api_endpoints.py
git commit -m "feat: expose the model and effort comparison over the api"
```

---

## Task 3: 前端对比区块

**Files:**
- Modify: `web/src/api/types.ts`、`web/src/api/endpoints.ts`
- Modify: `web/src/views/ContextView.vue`
- Test: `npm run build` + `scripts/nav-smoke.mjs`

**Interfaces:**

- Consumes: `GET /api/models`
- Produces: 「上下文成本」页新增「模型与推理强度对比」表 + 一张成本柱状图

- [ ] **Step 1: 类型与端点**

```ts
export interface ModelEffortStat {
  model: string
  effort: string
  calls: number
  turn_count: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  cache_hit_rate: number
  cost: number
  avg_cost_per_call: number
  avg_input_tokens: number
  unpriced_calls: number
  currency: string
}

export interface ModelComparisonResponse {
  range: { start: string; end: string; days: number }
  rows: ModelEffortStat[]
  currency: string
  total_cost: number
}
```

```ts
  models: (days: number, project?: string) =>
    http.get<ModelComparisonResponse>(`/models${query({ days, project })}`),
```

- [ ] **Step 2: 页面加图表 + 表**

在 `ContextView.vue` 追加第三个 `useAsync(() => api.models(days.value))`，渲染：

- `ChartCard title="按模型与强度的成本"`：柱状图，x 轴 `\`${model}/${effort}\``，y 轴 `cost`，
  颜色用 `utils/palette.ts` 的 `CHART_COLORS`；
- 一张表：模型 / 推理强度 / 调用次数 / 轮次数 / 平均每次 input token / cache 命中率 / 总成本 / 平均成本；
- 金额一律用 `formatCost`，且要带上 `currency`（沿用既有页面做法）；
- `unpriced_calls > 0` 时在表格里给出「有调用查不到价目表」的提示（与设置页口径一致）。

- [ ] **Step 3: 构建 + 浏览器验收**

```bash
cd web && npm run build
uv run agent-lens serve --db /private/tmp/p21.db --port 8000
BASE=http://127.0.0.1:8000 node scripts/nav-smoke.mjs
```
Expected: 构建成功；探针 7 步全过；`/api/models` 在真实库上返回 `deepseek-flash/low` 与
`deepseek-v4-pro/high` 两行以上（先期验证的 113 条 `turn_context` 支撑这一点）。

- [ ] **Step 4: Commit**

```bash
git add web/src
git commit -m "feat: compare model and effort cost on the context cost page"
```

---

## Self-Review（控制器已核对）

- **Spec 覆盖**：6.9 的「对比同一项目在不同推理强度下的成本，以及不同模型间的单位成本差异」→ Task 1 的
  `(model, effort)` 分组 + `avg_cost_per_call` / `avg_input_tokens`；9.1 的页面归属 → Task 3 落在上下文成本页。
- **不改 schema**：`turns.model` / `turns.effort` 已存在，本计划零迁移。
- **成本口径**：全部走 `_CostedRows`，与总览、项目、工具各页一致，避免出现两套金额。
