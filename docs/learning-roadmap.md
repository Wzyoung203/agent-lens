# agent-lens 学习路线

这份清单和开发任务一一对应。原则是：**不要先学完再做，做到哪块就学哪块**，每个知识点都标注了它会在哪个开发任务里立刻用到。

## 怎么用

- 每个开发任务开始前，花 1 到 2 小时过一遍对应小节的知识点，然后花 2 到 4 小时编码。
- 每节末尾有「自测」，答不上来就别往下走，那个点一定会在编码时卡住你。
- 学习资源优先级：**官方文档 > 动手实验 > 别人的博客 > 视频**。视频最容易让人产生"我会了"的错觉。
- 遇到卡点先记在一个 `notes/` 文件里，不要原地死磕超过 30 分钟。

---

## 阶段 0 · 开工前（半天）

### 知识点

- **Python 项目管理**：什么是虚拟环境、为什么需要它；`pyproject.toml` 和 lock 文件的区别；`uv` 是什么、为什么比 pip 快。
- **三个核心概念**：LLM observability、trace、span。看不懂这三个词，后面所有设计都读不顺。
- **JSONL 格式**：为什么日志用 JSONL 而不是一个大 JSON 数组（关键：可以逐行追加、逐行读取、单行损坏不影响其他行）。

### 自测

- 不看资料，说清 trace 和 span 的关系。
- 说出为什么我们的采集器不能把整个会话文件一次性读进内存再解析。

---

## 阶段 1 · 端到端最小闭环

### P1.1 解析器与数据模型

**要学的**

- Python 类型标注：`Optional`、`Literal`、`list[str]`、`from __future__ import annotations`
- pydantic v2：`BaseModel`、`Field`、`@model_validator`、`ValidationError`、为什么用 pydantic 而不是 dataclass（数据来自不可信的外部文件，需要校验）
- 生成器与惰性求值：`yield`、`Iterator`，为什么逐行读要用生成器
- `json` 模块的 `json.loads` 与异常处理
- 正则表达式基础：`re.compile`、`^` `$`、`MULTILINE` 标志、`re.Match`
- pytest：`@pytest.mark.parametrize`、`pytest.raises`
- TDD 循环：先写失败的测试，再写最小实现

**自测**

- 给你一条从没见过的 JSONL 行，你能写出对应的 pydantic 模型。
- 说清为什么 `turn_token_usage` 和 `thread_token_usage` 不能用来做总量统计。
- 手写一个 `@pytest.mark.parametrize` 测试，覆盖 3 个输入。

### P1.2 存储层

**要学的**

- SQLite 基础：表、主键、唯一约束、索引、外键
- `INSERT OR IGNORE` 与幂等的概念（同一操作执行多次，结果和执行一次相同）
- 事务与 `with` 上下文管理器
- WAL 模式是什么、为什么单写者场景下要用它
- Python `sqlite3` 模块：`connect`、`execute`、`executemany`、`row_factory`
- 水位（watermark）与断点续传的设计思路
- 基础聚合 SQL：`GROUP BY`、`SUM`、`COUNT`、`JOIN`

**自测**

- 说清为什么幂等键选 `(文件路径, ordinal)` 而不是 `session_id`。
- 写出一条 SQL：取每个项目在本月的总 input token。
- 说清 `INSERT` 和 `INSERT OR IGNORE` 在重复数据上的行为差异。

### P1.3 采集守护进程

**要学的**

- `asyncio` 基础：事件循环、`async def`、`await`、`asyncio.Task`、`asyncio.Queue`、`asyncio.sleep`
- 并发模型取舍：进程、线程、协程分别适合什么场景（本项目是 IO 密集 + 长驻）
- 文件增量读取：`seek`、`tell`、用文件大小和 mtime 判断是否有新内容
- 不完整数据处理：为什么读到半行要回退偏移而不是丢掉
- 可靠性模式：指数退避、重试上限、队列落盘、优雅退出（信号处理）
- 正则脱敏：误报与漏报的权衡，为什么宁可多报不可漏报
- Langfuse Python SDK：创建 trace、span、generation，`session_id` 与 `metadata` 怎么传
- 日志分级：DEBUG / INFO / WARNING / ERROR 各该记什么

**自测**

- 说清为什么不用 watchdog 事件监听，而用 1 到 2 秒轮询。
- 手写一个指数退避的重试函数（1s 起，每次翻倍，上限 5 分钟）。
- 说清 Langfuse 挂掉时采集器为什么不能跟着挂。

### P1.4 查询 API 与 Web 前端

**要学的**

- FastAPI：路由、路径参数与查询参数、pydantic 响应模型、依赖注入、CORS、`uvicorn` 启动
- SQL 进阶：日期分桶（按天聚合）、窗口函数、`ORDER BY` + `LIMIT` 做排行榜
- Vue 3 组合式 API：`ref`、`reactive`、`computed`、`watch`、`onMounted`
- Vue 组件：`props`、`emit`、插槽、`v-for` + `:key`、条件渲染
- Vue Router：路由与页面组织
- Vite：目录结构、开发服务器代理（解决前后端跨域）、环境变量
- Element Plus：布局容器、表格、表单、日期选择器、中文 locale 配置
- ECharts：柱状图、折线图、饼图、堆叠面积图、`resize` 响应式、中文数字格式化
- 接口契约：前后端如何约定字段名与分页

**自测**

- 写一条按天分桶、统计每日 token 总量的 SQL。
- 用 `computed` 派生一个"按项目过滤后的会话列表"。
- 说清前端为什么不该直接连 Langfuse 的 API。

---

## 阶段 2 · 分析深化

**要学的**

- Token 估算原理：为什么"字符数除以 3.5"只是近似；BPE 分词的基本直觉；中文与代码的 token 密度差异
- 上下文构成分析：把消息序列按角色（system / developer / user / assistant / tool）分类统计
- 数据可视化进阶：堆叠面积图表达"构成随时间变化"、瀑布图表达"耗时分布"
- 百分点与分布：为什么延迟要看 P50 / P95 / P99 而不是平均值
- 成本模型的可重算设计：事实表与维度表、为什么派生指标不该落库

**自测**

- 解释为什么固定指令"token 数量很大但成本不高"。
- 说清平均值为什么骗人，举一个真实例子。
- 解释"改价后历史趋势一致重算"在实现上靠什么保证。

---

## 阶段 3 · 传输效率与数据生命周期

**要学的**

- OpenTelemetry 三个信号：traces、metrics、logs，各自的用途
- OTLP 协议：HTTP 与 gRPC 两种传输，接收端怎么写
- 直方图（histogram）指标：为什么延迟类指标用直方图而不是计数器
- 拉取模型与推送模型：Prometheus 和 OTLP 的本质区别
- 压缩与归档：gzip、zstd 的取舍；为什么 JSONL 压缩率能到 14%
- 数据生命周期：保留期、归档、冷热分层

**自测**

- 说清 OTLP 推送模型和 Prometheus 拉取模型的区别。
- 解释为什么 TTFT 适合用直方图记录。
- 说清"归档前必须确认指标已入库"是为了防什么。

---

## 贯穿全程的工程习惯

这些不占单独的学习时间，而是在每个任务里刻意练习：

- **Git**：小步提交，一次提交只做一件事；commit message 写清"为什么"而不只是"做了什么"。
- **测试**：先写测试再写实现；测试失败时先确认失败原因是预期的那个。
- **调试**：卡住时先最小化复现，而不是改代码试运气；日志要能回答"刚才发生了什么"。
- **文档**：写代码时顺手更新 README 和设计文档，别攒到最后。
- **脱敏意识**：任何把外部数据写进日志或数据库的动作，先问一句"这里有密钥吗"。

## 求职向的练习

这个项目最终要能讲成三个故事，边做边攒素材：

1. **一个技术判断**：为什么把 Langfuse 定位成"可重建的视图"而不是数据库——因为免费版 30 天保留，这个约束倒逼出了可重放的数据管道。
2. **一个踩坑过程**：发现 `thread_token_usage` 跨文件重置，原本的总量统计算错了；怎么通过校验公式发现、怎么修正设计。
3. **一个差异化能力**：把工具调用失败折算成 token 成本——这是 Langfuse 给不了的视角。

每完成一个阶段，回头把这三个故事更新一遍，写进 README 或单独的 `docs/stories.md`。
