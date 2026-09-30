# 成本展示币种改为人民币 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 界面上的金额默认显示人民币（¥），而不是美元（$）。

**Architecture:** 价目表**存的是什么币种就是什么币种**（deepseek 官方价是美元，不动它）；
换算发生在**查价的那一刻**——`pricing._lookup_price()` 是全部成本计算的唯一入口
（`Pricer.cost_for` 与 `summarize_cost` 都走它），在那里把美元单价按汇率折成人民币单价，
下游的构成、按天、按项目、按模型所有金额自动跟着变。这与「成本不落库、查询时算」
的既有设计一致：改汇率后历史数据立刻按新汇率重算，库里不留任何人民币数字。

**Tech Stack:** Python 3.12、pydantic、SQLite、pytest、Vue 3 + TypeScript、uv。

**Spec:** 设计文档 6.4（成本不落库）、9.2（配置项）、6.9（模型对比）

**Depends on:** P1.4a 的 `pricing` 表与 `_CostedRows`

## Global Constraints

- **零 schema 变更**：`pricing.currency` 列早就存在，本计划不新增表/列，不改 SCHEMA_VERSION。
- **不写死价格**：库里存的单价一个数字都不改，换算只在读取时发生。
- 汇率是**配置项**（`[display] usd_to_cny`），默认 `7.1`，写进文档：这是一个假设值，
  用户随时可改，改完重启即生效。
- 已经是人民币的价目表行**不二次换算**（只换算 `currency == "USD"` 的行）。
- 成本算式的单元测试继续按美元断言（它们测的是算术，不是币种），换算另有专门用例。

## 裁决

1. **换算放在后端、不放前端**。前端只负责按 payload 里的 `currency` 选符号（`¥` / `$`）。
   理由：API 契约里本来就有 `currency` 字段，让字段说实话比让界面偷偷乘一个数好。
2. **默认币种定为 CNY**（代码默认值 + `deploy/config.toml` 都写 CNY），
   想回美元把 `[display] currency` 改成 `USD` 即可。
3. **汇率默认 7.1**：这是本轮唯一拍的数，落实在配置与文档里，用户可一行改掉。

---

## Task 1: 配置项 + 查价时换算

**Files:**
- Modify: `src/agent_lens/config.py`、`src/agent_lens/pricing.py`、`deploy/config.toml`
- Test: `tests/test_pricing_currency.py`（新增）、`tests/conftest.py`

**Interfaces:**

- `config.DisplayConfig(currency: Literal["USD","CNY"] = "CNY", usd_to_cny: float = 7.1)`
- `AppConfig.display: DisplayConfig`
- `pricing.display_config() -> DisplayConfig`（首次读取后缓存）、
  `pricing.set_display_config(value: DisplayConfig | None) -> None`（测试用）
- `pricing.convert_entry(entry: PriceEntry, display: DisplayConfig) -> PriceEntry`

- [ ] **Step 1: 写失败测试**

```python
def test_usd_prices_become_cny_when_display_is_cny(lens_db):
    upsert_price(lens_db, PriceEntry(provider="deepseek", model="m",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        input_price_per_mtok=1.0, cached_input_price_per_mtok=0.1,
        output_price_per_mtok=2.0, currency="USD"))
    set_display_config(DisplayConfig(currency="CNY", usd_to_cny=7.0))

    entry = price_at(lens_db, "deepseek", "m", now=datetime(2026, 9, 1, tzinfo=UTC))

    assert entry.currency == "CNY"
    assert entry.input_price_per_mtok == pytest.approx(7.0)


def test_cny_prices_are_not_converted_twice(lens_db): ...
def test_usd_display_keeps_the_original_numbers(lens_db): ...
def test_display_default_is_cny(): ...   # 断言 AppConfig() 的默认值
```

`tests/conftest.py` 加一个 autouse fixture：把 display 固定成 USD，
让既有的成本算式用例（`0.0054` 这类）继续按美元断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_pricing_currency.py -v`
Expected: `ImportError` / `AttributeError`（`DisplayConfig` 还不存在）。

- [ ] **Step 3: 实现**

`config.py` 加 `DisplayConfig` 与 `AppConfig.display`；`pricing._lookup_price()` 在
`_row_to_entry(row)` 之后过一道 `convert_entry(entry, display_config())`。
`deploy/config.toml` 补：

```toml
[display]
# 价目表按官方美元价存；这里只决定「算钱时折算成哪种货币显示」。
currency = "CNY"
usd_to_cny = 7.1
```

- [ ] **Step 4: 跑测试确认通过**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest -q`
Expected: 全绿（既有成本断言不受影响，因为它们被 fixture 固定成 USD）。

- [ ] **Step 5: 提交**

---

## Task 2: 让 payload 带币种 + 前端按 payload 显示

**Files:**
- Modify: `src/agent_lens/queries.py`（`ProjectStat.currency` / `SessionSummary.currency`）
- Modify: `web/src/utils/format.ts`（默认币种改 CNY）、`web/src/views/*.vue`
- Test: `tests/test_queries_projects.py` / `tests/test_api_endpoints.py` 补断言

**Interfaces:**

- `queries.ProjectStat.currency: str = ""`、`queries.SessionSummary.currency: str = ""`，
  由 `_CostedRows.currency(row)` 填充（查不到价目表就留空）。
- 前端：所有 `formatCost(...)` 调用点都要带上 payload 的 `currency`，不再依赖默认值。

- [ ] **Step 1: 后端加字段并断言**（`/api/projects` 与 `/api/projects/{name}` 的
  `project.currency == "CNY"`、`sessions[0].currency == "CNY"`）
- [ ] **Step 2: 前端把 currency 传全**（Projects / Sessions / SessionDetail / Tools / Overview 的
  裸 `formatCost(x)` 全部补 currency；`formatCost` 默认值改成 `'CNY'`）
- [ ] **Step 3: 构建 + 浏览器验收**

```bash
cd web && npm run build
BASE=http://127.0.0.1:8010 node scripts/nav-smoke.mjs
BASE=http://127.0.0.1:8010 node scripts/projects-click-smoke.mjs
```
Expected: 构建通过、探针全过；页面上金额显示为 `¥`。

- [ ] **Step 4: 提交**

---

## Task 3: 真实库回归 + 文档

- [ ] **Step 1:** 重建镜像 `docker compose up -d --build`，`curl /api/overview` 核对
  `cards.currency == "CNY"`、`total_cost` ≈ 旧美元值 × 7.1。
- [ ] **Step 2:** 设计文档 6.4 与 9.2 补一段「展示币种与汇率」；README 的「数据与配置」补
  `[display]` 说明。
- [ ] **Step 3:** 提交。

---

## Self-Review

- **诚实性**：库里仍是官方美元单价，界面显示的是按配置汇率折算的人民币；汇率与来源写在
  配置与 README 里，不是藏在代码里的魔数。
- **可逆**：`[display] currency = "USD"` 一行回到原样，历史数据不需要任何重算。
- **风险**：汇率是静态假设，不接实时汇率 API——本轮不做，记为已知边界。
