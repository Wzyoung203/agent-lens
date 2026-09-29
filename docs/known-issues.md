# 已知问题

发现即记录；**未标注「已修复」的都还没动过代码**。

## KI-1 · 点击左侧导航切换页面时内容区空白，刷新后才显示

| | |
|---|---|
| 记录时间 | 2026-09-28 |
| 状态 | **已修复**（2026-09-29，提交 `16fdaf3`，分支 `fix-ki-1-nav-blank`） |
| 报告人 | 用户（本地实机操作发现） |
| 定位 | 根因见下；修复方式是给过渡补一个单根容器 |
| 环境 | `docker compose up` 的 `web` 服务：生产构建 + FastAPI 同源托管，http://localhost:8000 |

### 现象

1. 打开 http://localhost:8000/ ，总览页正常。
2. 点击左侧任一导航项（项目 / 会话 / 工具调用 / 设置）。
3. 右侧内容区变**空白**：侧栏、顶栏、页面标题都正常更新（点「项目」标题就是「项目」），
   只有内容没了。
4. 在该地址按浏览器刷新，内容恢复正常。
5. 白屏之后再点别的导航项也回不来——一旦坏掉就是坏的，只有刷新能救。

| 点击导航后 | 刷新后 |
|---|---|
| ![空白](known-issues/ki-1-blank-after-nav.png) | ![正常](known-issues/ki-1-after-reload.png) |

### 根因（已用 Playwright 实测确认）

`<Transition>` **只能包裹单个根节点**，而 `web/src/App.vue` 把一个**多根节点的异步路由组件**
交给了 `<Transition mode="out-in">`：

```vue
<!-- web/src/App.vue：问题在这里 -->
<RouterView v-slot="{ Component }">
  <Transition name="al-fade" mode="out-in">
    <component :is="Component" />
  </Transition>
</RouterView>
```

每个视图的模板都是多根片段——例如 `views/OverviewView.vue` 是
`<PageHeader />` + `<StateBlock />` 两个并列根节点，`ProjectsView.vue` 同理。
多根 + `mode="out-in"` 组合下，过渡拿不到要进入的元素，于是新视图**根本没有被挂载**。

实测证据（Playwright 探测 `main.content`，硬加载 → 点击「项目」→ 再点「总览」）：

| 时刻 | `main.content` 子元素 | 文本长度 | innerHTML |
|---|---|---|---|
| 硬加载 `/` | 3 个（`div.head` / `div.cards` / `div.grid`） | 222 | 3572 字符 |
| 点击「项目」之后 | **0 个** | 0 | 只剩 `<!---->` |
| 再点「总览」之后 | **0 个** | 0 | 只剩 `<!---->` |

补充事实，排除常见误判：

- **没有** console 报错、`pageerror`、失败请求或 4xx 响应（生产构建会剥掉 Vue 的开发期警告，
  所以这条线索是哑的，但不代表没问题）。
- 不是数据问题：刷新后同一个页面渲染正常，接口全部 200。
- 不是 chunk 加载失败：客户端跳转时新视图的 JS 早已随首屏加载，且网络面板干净。

### 修复与验收（2026-09-29）

三个候选方向里选了第 3 个的变体：**在 `App.vue` 里给路由组件补一层按 `route.path` 做 key
的单根容器**，把它交给 `<Transition>`。只改一个文件，不动六个视图的布局，也保住了路由淡入。

```vue
<!-- web/src/App.vue -->
<RouterView v-slot="{ Component, route }">
  <Transition name="al-fade" mode="out-in">
    <div :key="route.path" class="route-view">
      <component :is="Component" />
    </div>
  </Transition>
</RouterView>
```

关键点是**容器必须带 key**：不带 key 时过渡前后是同一个元素，`mode="out-in"` 不会触发，
淡入淡出会静默失效。`.route-view` 只设 `display: block`，对 `main.content` 布局零影响。

验收证据（同一份探针，修复前 / 修复后各跑一次，真实采集 + 真实 `agent-lens serve` 同源托管）：

| 步骤 | 修复前 `main.content` 子元素 | 修复后 |
|---|---|---|
| 硬加载 `/` | 3 | 1 |
| 点击 项目 | **0** | 1 |
| 点击 会话 | **0** | 1 |
| 点击 工具调用 | **0** | 1 |
| 点击 设置 | **0** | 1 |
| 回到 总览 | **0** | 1 |
| 再点 项目 | **0** | 1 |

两次运行的 console error 与 page error 都是 0（这一点也和原报告一致——这个 bug 的迷惑性正在于
**没有任何报错**）。修复前的 `innerHTML` 只剩 `<!---->`，与 2026-09-28 的 Playwright 观测逐字吻合。

### 回归探针

`scripts/nav-smoke.mjs` 把上面的验收固化下来，用 CDP 直连 Playwright 缓存里的
Chrome for Testing，不需要给仓库装 JS 测试栈（仓库目前没有 vitest/jsdom，也不打算为一个冒烟
测试引进来）：

```bash
uv run agent-lens serve --port 8000          # 或 docker compose up web
BASE=http://127.0.0.1:8000 node scripts/nav-smoke.mjs
```

退出码非 0 就是「导航后内容区空白」这类回归。注意它**必须连真实服务**才能验证前端，
所以需要在允许监听端口的机器上跑（本仓库的 agent 沙箱禁止 listen）。
