# 已知问题

发现即记录；**未标注「已修复」的都还没动过代码**。

## KI-1 · 点击左侧导航切换页面时白屏，刷新后才显示

- 记录时间：2026-09-28
- 状态：**未修复**（按要求先记录，不动代码）
- 报告人：用户（本地实机操作发现）
- 环境：`docker compose up` 的 `web` 服务，即生产构建 + FastAPI 同源托管，
  浏览器访问 http://localhost:8000

**复现步骤**

1. 打开 http://localhost:8000/ ，总览页正常。
2. 点击左侧任一导航项（项目 / 会话 / 工具调用 / 设置）。
3. 页面变成**空白**。
4. 在该地址按浏览器刷新，内容正常显示。

**已知的观察**

- 直接硬加载（在地址栏敲该 URL）是正常的，只有 SPA 内部跳转后白屏。
- 尚未采集浏览器控制台报错与网络请求状态，因此根因未定。

**待查方向**（都是假设，未经验证，列出来免得下次从零开始）

1. `web/src/App.vue` 用的是 `<RouterView v-slot>` + `<Transition mode="out-in">`；
   路由组件是 `() => import(...)` 的异步组件。`mode="out-in"` 要求 leave 结束才 enter，
   异步组件解析与过渡时序竞争时可能停在「旧组件已走、新组件未挂」的空档。
   → 先验证：去掉 `Transition` 是否还白屏（对照实验，不改主干）。
2. 客户端跳转时新路由的 chunk 是否真的请求成功（DevTools → Network 看
   `assets/*View-*.js` 的状态码；FastAPI 的 `_spa_fallback` 会不会把资源请求也吞成 index.html）。
3. 各视图 `StateBlock` 在 `loading=false / error=null / empty=false` 之外的状态组合下
   是否渲染了空内容。
4. 图表容器在过渡期间高度为 0 时，`echarts.init` 可能抛错并中断该视图的渲染。

**复现与定位手段**：用 Playwright 打开页面、点击导航、截图并收集 `console` 与 `pageerror`。
