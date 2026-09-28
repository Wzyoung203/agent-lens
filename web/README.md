# agent-lens Web

Vue 3 + Vite + TypeScript + Element Plus + ECharts 的中文仪表盘，消费 P1.4a 的 `/api` 接口。

设计方向是**深空控制台**：简约、科技感、交互丝滑。三条落地规则写在
`docs/superpowers/plans/2026-09-28-p1-4b-web-frontend.md` 开头，视觉令牌在
`src/styles/tokens.css`（唯一来源，浅色是同构的第二形态）。

## 跑起来

开发模式（Vite dev server + `/api` 代理到 127.0.0.1:8000）：

```bash
# 终端 1：后端
cd .. && uv run agent-lens serve          # http://127.0.0.1:8000

# 终端 2：前端
npm install
npm run dev                                # http://127.0.0.1:5173
```

生产模式（前端产物由 FastAPI 托管，前后端同源，不需要 CORS）：

```bash
npm run build                              # 产出 dist/
cd .. && uv run agent-lens serve            # 默认托管 web/dist
```

`agent-lens serve --web-dir <PATH>` 可以指向别处的构建产物。

## 目录

| 路径 | 职责 |
|---|---|
| `src/api/` | 类型化客户端：`types.ts` 与后端 JSON 字段逐字对齐，`endpoints.ts` 是页面唯一的数据入口 |
| `src/composables/` | `useAsync`（加载/错误/空三态）、`usePolling`（10 秒，失焦暂停）、`useRange`（全局时间范围）、`useChart`、`useTheme` |
| `src/components/` | 指标卡、图表容器、状态块等通用件 |
| `src/layout/` | 侧栏 + 顶栏外壳 |
| `src/views/` | 五个页面：总览、项目、会话（含详情）、工具调用、设置 |
| `src/utils/` | 金额/数量/百分比的格式化与图表色板 |

## 约定

- 字段名与接口**原样一致**（snake_case），不做驼峰转换；契约在
  `docs/superpowers/plans/2026-09-28-p1-4a-query-api.md` 的「前端契约」一节。
- 不直连 Langfuse，状态由 `/api/settings/status` 代为汇报。
- 所有请求走 `/api` 相对路径。
- 金额用 `formatCost` 自适应精度：deepseek 单价很低，直接 `toFixed(2)` 会把真实花费显示成 `$0.00`。
- 图表容器带 `role="img"` 与 `aria-label`，失败率同时给数字，不靠颜色单独表达。
