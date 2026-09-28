# P1.4b Web 前端（Vue 3）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Vue 3 + TypeScript + Element Plus + ECharts 做一个中文优先的深色仪表盘，把 P1.4a 的聚合接口变成五个页面：总览、项目、会话、工具、设置。

**Architecture:** `web/` 是与后端同构的独立前端工程，产物 `web/dist` 由 FastAPI 托管（P1.4a Task 7）。设计层先立一套 CSS 变量令牌（深色为默认，浅色同构），Element Plus 全量走令牌覆盖，ECharts 走统一的主题工厂；页面只消费 `api/endpoints.ts` 的类型化函数，不直接 `fetch`。数据加载统一走 `useAsync`（含 loading / error / empty 三态）+ `usePolling`（10 秒，设计文档 9.4）。

**Tech Stack:** Node 20+、Vite 6、Vue 3.5（`<script setup>` + TS）、Vue Router 4、Element Plus 2（中文 locale）、ECharts 5、`vue-tsc`。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（重点 9.1 页面清单、9.2 项目归属、9.3 技术选型、9.4 实时性、9.5 语言）
**接口契约:** `docs/superpowers/plans/2026-09-28-p1-4a-query-api.md` 的「前端契约」一节——字段名一律 snake_case，与 JSON 原样对应，前端不做驼峰转换。

---

## 设计方向：深空控制台（Deep-Space Console）

用户给的方向是**简约、科技感、交互丝滑**。落地成三条可检验的规则：

1. **数据是主角，界面退到背景。** 没有插画、没有大面积品牌色块、没有立体投影。卡片是 1px 发丝边框 + 极低对比度的表面色，页面里最亮的东西永远是数字和图表。
2. **科技感来自材质，不来自装饰。** 近黑底（带一丝冷蓝）、卡片表面微微提亮、活跃元素带一圈极淡的青色辉光（`box-shadow`，不是描边动画）。禁用彩虹渐变、禁用发光文字、禁止霓虹描边堆叠。全站只有一个主强调色（青）和一个次强调色（紫），其余颜色只服务于数据语义（成功/警告/危险）。
3. **丝滑是时间函数。** 所有状态变化走 140–320ms 的统一缓动；数字变化做滚动增长；图表入场 400ms；路由切换淡入 180ms；加载态一律骨架屏 shimmer，禁止 spinner 闪烁造成的布局跳动。所有 hover 抬升不超过 2px，绝不移动整块布局。

### 视觉令牌（`src/styles/tokens.css`，逐字实现）

```css
:root {
  /* 浅色（默认）——同构的第二形态，不是另一套设计 */
  --al-bg: #f5f7fb;
  --al-bg-glow: radial-gradient(1100px 520px at 12% -12%, rgba(34, 211, 238, 0.10), transparent 62%),
                radial-gradient(900px 460px at 102% -4%, rgba(139, 92, 246, 0.08), transparent 58%);
  --al-surface: #ffffff;
  --al-surface-2: #f2f5fa;
  --al-surface-3: #e9eef6;
  --al-border: rgba(15, 23, 42, 0.08);
  --al-border-strong: rgba(15, 23, 42, 0.16);
  --al-text: #0f172a;
  --al-text-2: #475569;
  --al-text-3: #8492a6;
  --al-accent: #0891b2;
  --al-accent-2: #7c3aed;
  --al-success: #059669;
  --al-warning: #d97706;
  --al-danger: #dc2626;
  --al-info: #2563eb;
  --al-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 10px 28px rgba(15, 23, 42, 0.07);
  --al-glow: 0 0 0 1px rgba(8, 145, 178, 0.35), 0 0 20px rgba(8, 145, 178, 0.14);
  --al-accent-soft: rgba(8, 145, 178, 0.10);
}

html.dark {
  --al-bg: #0a0e17;
  --al-bg-glow: radial-gradient(1100px 520px at 12% -12%, rgba(34, 211, 238, 0.10), transparent 62%),
                radial-gradient(900px 460px at 102% -4%, rgba(139, 92, 246, 0.09), transparent 58%);
  --al-surface: #10151f;
  --al-surface-2: #161d2a;
  --al-surface-3: #1d2534;
  --al-border: rgba(255, 255, 255, 0.07);
  --al-border-strong: rgba(255, 255, 255, 0.15);
  --al-text: #e8edf7;
  --al-text-2: #97a2b8;
  --al-text-3: #5e6980;
  --al-accent: #22d3ee;
  --al-accent-2: #8b5cf6;
  --al-success: #34d399;
  --al-warning: #fbbf24;
  --al-danger: #f87171;
  --al-info: #60a5fa;
  --al-shadow: 0 1px 2px rgba(0, 0, 0, 0.45), 0 10px 28px rgba(0, 0, 0, 0.32);
  --al-glow: 0 0 0 1px rgba(34, 211, 238, 0.30), 0 0 22px rgba(34, 211, 238, 0.16);
  --al-accent-soft: rgba(34, 211, 238, 0.12);
}

:root {
  /* 形状与节奏 */
  --al-radius: 12px;
  --al-radius-sm: 8px;
  --al-radius-pill: 999px;
  --al-space-1: 4px;
  --al-space-2: 8px;
  --al-space-3: 12px;
  --al-space-4: 16px;
  --al-space-5: 24px;
  --al-space-6: 32px;
  --al-ease: cubic-bezier(0.22, 0.61, 0.36, 1);
  --al-dur-fast: 140ms;
  --al-dur: 200ms;
  --al-dur-slow: 320ms;
  --al-font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
             "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  --al-font-mono: "SF Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}

@media (prefers-reduced-motion: reduce) {
  :root { --al-dur-fast: 0ms; --al-dur: 0ms; --al-dur-slow: 0ms; }
}
```

**数据色板（唯一来源，`src/utils/palette.ts`）：**

```ts
export const CHART_COLORS = [
  '#22d3ee', '#8b5cf6', '#34d399', '#fbbf24', '#f87171',
  '#60a5fa', '#2dd4bf', '#f472b6', '#a3e635', '#fb923c',
]
export const SEMANTIC = { success: '#34d399', warning: '#fbbf24', danger: '#f87171', info: '#60a5fa' }
```

**排版规则：** 正文 14/22；卡片标题 13/20、字重 500、色 `--al-text-2`；指标数字 30/36、字重 600、`font-family: var(--al-font-mono)`、`font-variant-numeric: tabular-nums`；表格数字列一律右对齐 + 等宽数字。

**金额格式（重要）：** deepseek 单价是「每百万 token 零点几美元」，个人月账单常在 0.01–5 美元之间。`formatCost` 必须自适应精度：`>= 1` 保留 2 位；`>= 0.01` 保留 3 位；`>= 0.0001` 保留 4 位；再小则用 `$1.2e-5` 风格的科学计数法。**禁止**直接把 `$0.00` 显示给用户。

### 布局

```
┌──────────────────────────────────────────────────────────────┐
│ SideNav 240px │ TopBar: 页面标题 + 时间范围 + 刷新 + 主题切换 │
│               ├──────────────────────────────────────────────┤
│  总览          │                                              │
│  项目          │   Content（max-width 1440，左右 24px）        │
│  会话          │                                              │
│  工具          │                                              │
│  设置          │                                              │
└──────────────────────────────────────────────────────────────┘
```

- 侧栏 240px，可折叠到 64px（仅图标 + tooltip），状态存 `localStorage`。
- 顶栏高 56px，吸顶，底部 1px 发丝边框 + `backdrop-filter: blur(12px)`。
- 内容区背景用 `--al-bg` 叠 `--al-bg-glow`，`min-height: 100vh`。
- 断点：`<= 1200px` 侧栏自动折叠；`<= 900px` 顶栏的时间范围收进下拉、卡片从 4 列变 2 列；`<= 640px` 单列。

### Element Plus 覆盖策略（`src/styles/element-overrides.css`）

- `import 'element-plus/dist/index.css'` + `import 'element-plus/theme-chalk/dark/css-vars.css'`，通过 `document.documentElement.classList.toggle('dark')` 切换。
- 只覆盖这些变量：`--el-color-primary`（= `--al-accent`）、`--el-bg-color`、`--el-bg-color-overlay`、`--el-fill-color-blank`、`--el-text-color-primary/regular/secondary`、`--el-border-color`、`--el-border-color-light`、`--el-border-radius-base`（10px）。
- 表格：`--el-table-border-color: transparent`、行 hover 用 `--al-surface-2`、表头背景透明 + 字色 `--al-text-2`。
- 组件级微调：`el-card` 去掉默认阴影换成 `--al-shadow`；`el-button` 圆角 `--al-radius-sm`；`el-tag` 圆角 pill。

---

## Global Constraints

- 界面**中文优先**（设计文档 9.5）；标识符用英文，注释用中文。
- 字段名与 JSON **原样一致（snake_case）**，不引入转换层。
- 不直连 Langfuse（设计文档 9.1）；不引入 Pinia / axios / lodash（用 Vue 响应式 + `fetch` 足够）。
- 所有请求走 `/api` 相对路径（开发靠 Vite 代理，生产靠同源托管）。
- 轮询间隔 10 秒（设计文档 9.4），窗口失焦时暂停（`document.visibilityState`）。
- 无障碍：所有图标按钮带 `aria-label`；颜色不作为唯一信息载体（失败率同时给数字）；图表容器带 `role="img"` 与 `aria-label`。
- 不提交 `node_modules/` 与 `web/dist/`（已在 `.gitignore`，Task 1 核对并补全）。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `web/package.json`、`web/vite.config.ts`、`web/tsconfig*.json`、`web/index.html`、`web/env.d.ts` | 工程脚手架（Task 1） |
| `web/src/main.ts`、`web/src/App.vue`、`web/src/router/index.ts` | 入口、路由、全局挂载（Task 1） |
| `web/src/styles/tokens.css`、`element-overrides.css`、`base.css` | 设计系统（Task 1） |
| `web/src/layout/AppShell.vue`、`SideNav.vue`、`TopBar.vue` | 布局外壳（Task 1） |
| `web/src/composables/useTheme.ts` | 主题切换 + 持久化（Task 1） |
| `web/src/api/client.ts`、`types.ts`、`endpoints.ts` | 类型化 API 客户端（Task 2） |
| `web/src/composables/useAsync.ts`、`usePolling.ts`、`useRange.ts`、`useChart.ts` | 数据与图表组合式函数（Task 2） |
| `web/src/utils/format.ts`、`palette.ts` | 金额/数量/百分比格式化、色板（Task 2） |
| `web/src/components/MetricCard.vue`、`CostValue.vue`、`TokenValue.vue`、`PercentValue.vue`、`DeltaTag.vue` | 指标呈现（Task 2） |
| `web/src/components/ChartCard.vue`、`EChart.vue`、`StateBlock.vue`、`EmptyState.vue`、`SkeletonBlock.vue`、`PageHeader.vue` | 通用容器与状态（Task 2） |
| `web/src/views/OverviewView.vue` | 总览（Task 3） |
| `web/src/views/ProjectsView.vue` | 项目（Task 4） |
| `web/src/views/SessionsView.vue`、`SessionDetailView.vue` | 会话列表与详情（Task 5） |
| `web/src/views/ToolsView.vue` | 工具（Task 6） |
| `web/src/views/SettingsView.vue` | 设置（Task 7） |
| `web/README.md` | 前端跑法与构建说明（Task 8） |

---

### Task 1: 脚手架、设计系统与布局外壳

**Files:**

- Create: `web/package.json`、`web/vite.config.ts`、`web/tsconfig.json`、`web/tsconfig.node.json`、`web/index.html`、`web/env.d.ts`、`web/src/main.ts`、`web/src/App.vue`
- Create: `web/src/styles/{tokens.css,element-overrides.css,base.css}`
- Create: `web/src/layout/{AppShell.vue,SideNav.vue,TopBar.vue}`
- Create: `web/src/composables/useTheme.ts`、`web/src/router/index.ts`
- Create: 五个占位视图 `web/src/views/{OverviewView,ProjectsView,SessionsView,ToolsView,SettingsView}.vue`
- Modify: `.gitignore`

**Interfaces:**

- Produces: `npm run dev`（Vite，`/api` 代理到 `http://127.0.0.1:8000`）、`npm run build`（`vue-tsc -b && vite build`，产物 `web/dist`）、`npm run preview`
- Produces: `useTheme()` → `{ theme: Ref<'dark' | 'light'>, toggle(), setTheme(t) }`，初始值 `localStorage['al-theme'] ?? 'dark'`，写入时同步 `document.documentElement.classList`
- Produces: 路由表（`createWebHistory`）：`/`、`/projects`、`/sessions`、`/sessions/:sessionId`、`/tools`、`/settings`

**步骤要点：**

- [ ] **Step 1**：`npm create vite@latest` 等价的**手写**脚手架（不要交互式 CLI）：`package.json` 依赖固定为 `vue@^3.5`、`vue-router@^4.4`、`element-plus@^2.8`、`echarts@^5.5`、`@element-plus/icons-vue@^2.3`；devDeps 为 `vite@^6`、`@vitejs/plugin-vue@^5`、`typescript@^5.6`、`vue-tsc@^2.1`、`@types/node`。
- [ ] **Step 2**：`npm install`（需要联网，走提权）。
- [ ] **Step 3**：写 `tokens.css`（上面逐字）、`element-overrides.css`、`base.css`（`body` 背景 = `--al-bg` + `--al-bg-glow`；滚动条细样式；`::selection` 用 `--al-accent-soft`）。
- [ ] **Step 4**：`AppShell` 用 CSS Grid：`grid-template-columns: var(--al-sidebar, 240px) 1fr;`；`SideNav` 折叠时写 `--al-sidebar: 64px`。
- [ ] **Step 5**：`main.ts` 挂载 Element Plus（`app.use(ElementPlus, { locale: zhCn })`）、图标、路由、样式，并在挂载前应用主题（避免闪白）。
- [ ] **Step 6**：`.gitignore` 确认含 `node_modules/`、`dist/`、`.vite/`（已存在，核对即可）。
- [ ] **Step 7**：验证：`cd web && npm run build` 通过（`vue-tsc` 零错误）；`npm run dev` 起得来且 `/` 返回外壳。
- [ ] **Step 8**：提交 `feat: scaffold the web app with the deep-space design system`。

---

### Task 2: API 客户端、格式化工具与通用组件

**Files:**

- Create: `web/src/api/{client.ts,types.ts,endpoints.ts}`
- Create: `web/src/utils/{format.ts,palette.ts}`
- Create: `web/src/composables/{useAsync.ts,usePolling.ts,useRange.ts,useChart.ts}`
- Create: `web/src/components/{MetricCard,CostValue,TokenValue,PercentValue,DeltaTag,ChartCard,EChart,StateBlock,EmptyState,SkeletonBlock,PageHeader}.vue`

**Interfaces:**

- `client.ts`：`apiGet<T>(path: string, params?: Record<string, unknown>): Promise<T>`；非 2xx 抛 `ApiError { status, detail }`；`params` 里 `undefined/null/''` 的键不发送。
- `types.ts`：逐字复刻 P1.4a 的响应模型为 TS interface（`MetricCards`、`DailyPoint`、`ProjectStat`、`OverviewResponse`、`RangeInfo`、`SessionSummary`、`TurnSummary`、`ApiCallRow`、`ToolCallRow`、`SessionDetail`、`TurnDetail`、`ToolStat`、`ToolFailure`、`ToolsResponse`、`CostBreakdown`、`Page<T>`、`ProjectMapping`、`AppStatus`、`PriceEntry`）。
- `endpoints.ts`：每个接口一个函数，签名固定：
  ```ts
  export const getOverview = (p: OverviewParams) => apiGet<OverviewResponse>('/overview', p)
  export const getProjects = (p: { days?: number }) => apiGet<ProjectStat[]>('/projects', p)
  export const getProject = (name: string, p: { days?: number }) =>
    apiGet<ProjectDetail>(`/projects/${encodeURIComponent(name)}`, p)
  export const getSessions = (p: SessionQuery) => apiGet<Page<SessionSummary>>('/sessions', p)
  export const getSession = (id: string) => apiGet<SessionDetail>(`/sessions/${encodeURIComponent(id)}`)
  export const getTurn = (id: string, turnId: string) =>
    apiGet<TurnDetail>(`/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}`)
  export const getTools = (p: { days?: number; project?: string }) => apiGet<ToolsResponse>('/tools', p)
  export const getToolFailures = (p: ToolFailureQuery) => apiGet<Page<ToolFailure>>('/tools/failures', p)
  export const getSettingsStatus = () => apiGet<AppStatus>('/settings/status')
  export const getPricing = () => apiGet<PriceEntry[]>('/settings/pricing')
  export const putPricing = (body: PriceEntry) => apiPut<PriceEntry>('/settings/pricing', body)
  export const deletePricing = (p: PriceEntryKey) => apiDelete<{ deleted: boolean }>('/settings/pricing', p)
  export const getProjectMappings = () => apiGet<ProjectMapping[]>('/settings/projects')
  export const putProjectMapping = (body: { path_prefix: string; project: string }) =>
    apiPut<ProjectMapping>('/settings/projects', body)
  export const deleteProjectMapping = (path_prefix: string) =>
    apiDelete<{ deleted: boolean; sessions_reassigned: number }>('/settings/projects', { path_prefix })
  export const refreshProjects = () =>
    apiPost<{ sessions_reassigned: number }>('/settings/projects/refresh')
  ```
- `useAsync(fetcher, { immediate = true, deps })` → `{ data, error, loading, refresh() }`；`deps` 变化时重新拉取；竞态用递增请求号丢弃过期响应。
- `usePolling(refresh, intervalMs = 10000)` → `start()/stop()`，`visibilitychange` 时暂停/恢复；`onUnmounted` 自动停。
- `useRange()` → 模块级单例 `{ days: Ref<number>, setDays(n) }`，默认 30，持久化到 `localStorage['al-range']`。
- `format.ts`：`formatCost(v, currency)`（自适应精度，见上）、`formatTokens(v)`（`1.24M` / `34.5K` / `812`）、`formatPercent(v, digits = 1)`、`formatDuration(ms)`（`820ms` / `12.4s` / `3m12s`）、`formatDateTime(iso)`、`formatRelative(iso)`（`3 分钟前`）。
- `EChart.vue`：props `{ option: EChartsOption, height?: string }`；内部 `echarts.init` + `ResizeObserver`；`watch(theme)` 时 `dispose` 重建（ECharts 主题切换必须重建实例）；卸载时 `dispose`。
- `useChart.ts`：`chartTheme()` 返回当前主题下的 `{ textColor, axisLine, splitLine, colors, tooltip }`，供各页面拼 option。
- `MetricCard.vue`：props `{ label, value, unit?, hint?, trend?: number, loading?, accent?: 'default'|'accent' }`；数字用滚动增长（`requestAnimationFrame` 缓动 600ms，`prefers-reduced-motion` 时直接赋值）。
- `StateBlock.vue`：props `{ loading, error, empty }` + 插槽；loading → `SkeletonBlock`，error → 错误卡片 + 重试按钮，empty → `EmptyState`。

**步骤要点：**

- [ ] **Step 1**：先写 `format.ts` 与 `palette.ts` 的纯函数，再写 `client.ts`。
- [ ] **Step 2**：写 `types.ts`（与 P1.4a 契约逐字对齐），`vue-tsc` 必须零错误。
- [ ] **Step 3**：写通用组件；`EChart.vue` 必须处理「容器宽度为 0（页签未激活）」——`ResizeObserver` 回调里 `chart.resize()`。
- [ ] **Step 4**：验证：`npm run build`；`npm run dev` 起一个临时页面手动 `getOverview()` 打印到 console，确认代理生效与错误分支。
- [ ] **Step 5**：提交 `feat: add typed api client, formatters and shared ui kit`。

---

### Task 3: 总览页

**Files:**

- Modify: `web/src/views/OverviewView.vue`

**数据：** `getOverview({ days })`

**布局（自上而下）：**

1. 四张 `MetricCard`（响应式 4 / 2 / 1 列）：**本月花费**（`formatCost(cards.total_cost)`，hint 显示 `price 口径：当前价目表`）、**Token 总量**（`formatTokens(cards.total_tokens)`，hint `输入 x · 输出 y`）、**Cache 命中率**（`formatPercent(cards.cache_hit_rate)`，hint `缓存命中 x / 输入 y`）、**会话数**（`cards.session_count`，hint `turn x · 调用 y`）。`cards.unpriced_calls > 0` 时在花费卡片下方显示一条 warning 提示「有 N 次调用查不到价目表」并链到设置页。
2. **按天趋势** `ChartCard`：堆叠面积图（输入 token、输出 token，左轴）+ 折线（成本，右轴，虚线，青→紫渐变）。X 轴日期只显示 `MM-DD`；`tooltip` 里同时给三类数值与命中率；`dataZoom` 不启用（数据点少）。
3. **项目消费排行**：左侧横向条形图 Top 8（成本降序，条形色用 `CHART_COLORS` 轮转），右侧表格（项目 / 成本 / token / 命中率 / 会话数）带排序，行点击跳 `/projects`（带 `?project=` 查询参数）。

**交互：**

- 顶部时间范围切换（7 / 30 / 90 天）驱动 `useRange`，两个图表与卡片一起刷新。
- 数据加载中显示骨架屏（卡片 4 个 + 图表 2 个），失败显示重试按钮。
- 10 秒轮询（`usePolling`），刷新时不闪骨架屏——只有首次加载显示骨架，后续刷新做「静默替换」。

**步骤要点：**

- [ ] **Step 1**：实现页面，网络层只调 `getOverview`。
- [ ] **Step 2**：验证：`npm run build`；对着 P1.4a 的 `agent-lens serve` 用真实/种子数据目视核对数字与 `overview.cards` 一致（若后端尚未就绪，允许用 `web/dev/mock/overview.json` 临时 fixture 并明确标注，但**不提交**该 mock）。
- [ ] **Step 3**：提交 `feat: add the overview dashboard page`。

---

### Task 4: 项目页

**Files:**

- Modify: `web/src/views/ProjectsView.vue`

**数据：** `getProjects({ days })`、`getProject(name, { days })`，需要 `ProjectDetail`（P1.4a Task 4 产出：`{project, range, cards, daily, sessions, cost_breakdown}`）。

**布局：** 左栏 280px 项目列表（项目名 + 成本 + 会话数，选中态左侧 2px 青色指示条 + 表面提亮），右栏：

1. 项目头部：项目名、`PageHeader` 右侧显示成本与 token 总量。
2. 成本构成：环形图（未命中输入 / 命中输入 / 输出）+ 图例（金额 + 占比），配色固定 `SEMANTIC` 之外的三色（`#fbbf24` 未命中、`#22d3ee` 命中、`#8b5cf6` 输出）。
3. 该项目按天趋势（同总览的堆叠面积图，单项目版）。
4. 会话列表 `el-table`：会话 ID（前 8 位 + 复制按钮）、起止时间、turn 数、token、成本、命中率；行点击进 `/sessions/:id`。

**交互：** 左栏切换项目时右栏整体切 loading；URL 同步 `?project=`（刷新可复现），默认选成本最高的项目。空项目（未归类）也要能选。

**步骤要点：**

- [ ] **Step 1**：实现；项目列表与详情分别用两个 `useAsync`，切换项目时只重取详情。
- [ ] **Step 2**：验证 `npm run build` + 目视核对。
- [ ] **Step 3**：提交 `feat: add the project explorer page`。

---

### Task 5: 会话列表与会话详情页

**Files:**

- Modify: `web/src/views/SessionsView.vue`
- Modify: `web/src/views/SessionDetailView.vue`

**数据：** `getSessions({ project, search, limit, offset, days })`、`getSession(id)`、`getTurn(id, turnId)`（按需展开时拉取）。

**会话列表页布局：** 顶部筛选行（项目下拉 + 搜索框 + 时间范围）+ `el-table`：会话 ID、项目、开始时间、turn 数、api 调用数、工具调用数、token、成本、命中率。`el-pagination` 服务端分页（`limit=20`）。

**会话详情页布局（设计文档 9.1 的 turn 时间线）：**

- 顶部会话摘要条：项目、`cwd`、`cli_version`、模型、总成本、token、命中率、起止时间。
- 主体是一条竖向时间线（自绘 CSS，不用 el-timeline 的默认样式）：每个 turn 一行卡片，左侧竖直连线 + 圆点（失败/中断的 turn 用 `--al-danger` 圆点），卡片内：轮次序号、模型 + effort 标签、耗时、api 调用数、工具调用数（失败数用红色角标）、input/output token、cache 命中率进度条、成本、上下文膨胀率 `x1.8`（>2 时标黄）。点击卡片展开 `getTurn`，在卡片下方插入该轮的 `api_calls` 表格与 `tool_calls` 表格（工具结果摘要列超长截断 + tooltip 全文，`result_summary` 已脱敏）。
- 中断的轮次（`aborted_reason` 非空）显示一条 danger 横条。

**交互：** 展开是懒加载（点击才请求），展开/收起用 `max-height` 过渡（≥160ms）；同一时刻允许多个展开；`api_calls` 表格按 `ordinal` 升序。

**步骤要点：**

- [ ] **Step 1**：先做列表页，再做详情页。
- [ ] **Step 2**：验证 `npm run build`；确认展开一次不再重复请求（缓存已拉取的 turn）。
- [ ] **Step 3**：提交 `feat: add session list and turn timeline pages`。

---

### Task 6: 工具页

**Files:**

- Modify: `web/src/views/ToolsView.vue`

**数据：** `getTools({ days, project })`

**布局：**

1. 顶部两张卡：**工具调用总数**、**失败折算成本**（`formatCost(failure_cost.total)`，hint 说明口径：「失败之后该轮内所有调用的输入成本」）+ 一句解释性副文案。
2. **工具类型分布**：环形图（按 `call_count`）+ 图例。
3. **工具排行表** `el-table`：工具名、调用数、失败数、失败率（数字 + 迷你进度条）、平均耗时、P95 耗时、输出字符总量；支持按各列排序；失败率 > 10% 的行给淡红底。
4. **跨项目失败率**：分组条形图（项目 × 失败率），只在 `by_project` 非空时显示。
5. **失败清单** `el-table`（服务端分页，`limit=20`）：项目、工具名、退出码、耗时、结果摘要（脱敏后，超长截断 + tooltip）、所属会话（可点击跳会话详情）。

**交互：** 项目筛选下拉（含「全部」）；时间范围与全站共享。

**步骤要点：**

- [ ] **Step 1**：实现；失败清单单独走 `getToolFailures` 分页接口。
- [ ] **Step 2**：验证 `npm run build` + 目视核对。
- [ ] **Step 3**：提交 `feat: add the tool analytics page`。

---

### Task 7: 设置页

**Files:**

- Modify: `web/src/views/SettingsView.vue`

**数据：** `getPricing` / `putPricing` / `deletePricing`、`getProjectMappings` / `putProjectMapping` / `deleteProjectMapping` / `refreshProjects`、`getSettingsStatus`

**布局（四个分区卡片，纵向排列）：**

1. **价目表**：`el-table` 展示 provider / model / effective_from / time_window（`any`→「通用」、`peak`→「高峰」、`idle`→「空闲」标签着色）/ 三档价格 / 币种；行内「编辑」「删除」。上方「新增」按钮打开 `el-dialog` 表单（模型名、生效时间 `el-date-picker`、时段 `el-select`、三个价格 `el-input-number` 精度 6）。同主键保存即覆盖，保存后自动刷新列表并给 `ElMessage.success`。
2. **项目映射**：`el-table` 展示项目 / 路径前缀 / 会话数；支持新增（项目名 + 路径前缀）、删除。删除与「重算归属」按钮调用接口后，用 `ElMessage` 报出 `sessions_reassigned`。附一段说明文字解释三种归属规则的优先级（设计文档 9.2）。
3. **保留策略**：从 `/api/settings/status` 的字段与设计文档 7 节渲染为只读说明卡（`metrics_keep=forever`、`raw_keep_days=14`、默认只归档不删除），并注明「归档功能属阶段 3」。
4. **系统状态**：`getSettingsStatus` 的 `counts` 用定义列表展示（表名 → 行数），`parse_errors > 0` 时高亮；`langfuse_enabled` 用 `StatusDot` 显示「已启用 / 未启用」；显示 `db_path` 与 `sessions_dir`（等宽字体，长路径可换行），并注明「前端不直连 Langfuse，状态由本地服务汇报」。

**步骤要点：**

- [ ] **Step 1**：实现；所有写操作成功后局部刷新对应列表（不整页 reload）。
- [ ] **Step 2**：验证 `npm run build`；对种子库做一次「改价 → 回总览确认成本变了」的闭环检查（这是设计文档 6.4「改价重算」的可见证据）。
- [ ] **Step 3**：提交 `feat: add the settings page for pricing and project mapping`。

---

### Task 8: 动效打磨、响应式、构建产物与集成冒烟

**Files:**

- Modify: 全部视图（动效与响应式细节）
- Create: `web/README.md`
- Modify: `README.md`（根，补前端跑法）

**内容：**

- **路由过渡**：`App.vue` 里 `<RouterView>` 外包 `<Transition name="al-fade" mode="out-in">`，`al-fade` = opacity + 4px 位移，180ms；`prefers-reduced-motion` 下关闭。
- **卡片入场**：首屏卡片用交错 `animation-delay`（0/40/80/120ms）淡入上移 6px，只跑一次。
- **骨架屏**：`SkeletonBlock` 用 `linear-gradient` shimmer（1.4s 循环），颜色随主题。
- **响应式核对**：1280 / 1024 / 768 三档目视检查，修掉横向滚动、表格溢出（`el-table` 加 `max-height` + 固定列）。
- **构建**：`npm run build`，确认 `web/dist/index.html` + `web/dist/assets/*` 存在，`index.html` 里的资源路径是相对路径（`base: './'`，因为由 FastAPI 从任意前缀托管）。
- **集成冒烟**：`uv run agent-lens serve`（带种子库）→ 浏览器/curl 打开 `/` 得到前端外壳、`/api/overview` 得到 JSON、前端五个页面都能渲染出非空数据；把这一步的命令与结果写进 `web/README.md`。

**步骤要点：**

- [ ] **Step 1**：打磨动效与响应式。
- [ ] **Step 2**：写 `web/README.md`：依赖版本、`npm install`、`npm run dev`（代理说明）、`npm run build`、`agent-lens serve` 托管流程。
- [ ] **Step 3**：端到端冒烟（后端已由 P1.4a Task 7 提供 `serve`）。
- [ ] **Step 4**：提交 `feat: polish motion and responsiveness, document the web app`。

---

## Self-Review

### Spec coverage

| 设计文档要求 | 覆盖位置 |
|---|---|
| 9.1 总览（四卡 + 按天趋势 + 项目排行） | Task 3 |
| 9.1 项目视图（左列表 + 趋势 + 会话 + 成本构成） | Task 4 |
| 9.1 会话详情（turn 时间线，可展开到 api_call） | Task 5 |
| 9.1 工具调用（分布与排名、均值/P95/失败率、失败清单、跨项目对比、失败折算成本） | Task 6 |
| 9.1 设置（价目表、项目映射、保留策略、Langfuse 状态） | Task 7 |
| 9.2 项目归属三种规则的可解释性 | Task 7 说明卡 + Task 4 的「未归类」可选 |
| 9.3 Vue 3 + Vite + TS + Element Plus + ECharts | Task 1 |
| 9.4 10 秒轮询 | Task 2 `usePolling` + Task 3 接线 |
| 9.5 界面中文优先 | 全部视图 |

上下文成本分解 / skill 命中 / 模型与推理强度对比属阶段 2；传输效率页属阶段 3 —— 本计划不建这两个页面。

### Type consistency

- `types.ts` 的字段名与 P1.4a 的 pydantic 模型逐字相同（snake_case），包括 `cost_breakdown` 的四个键。
- `endpoints.ts` 的函数签名与 `types.ts` 的泛型一一对应；页面只 import `endpoints.ts`，禁止直接 `fetch`。
