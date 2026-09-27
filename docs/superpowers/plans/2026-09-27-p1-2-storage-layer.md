# P1.2 存储层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P1.1 解析出的 `ParsedSession` 幂等写进 SQLite，维护每个文件与每个会话的读取水位和项目归属，并提供「查询时计算」的成本模型。

**Architecture:** 三块职责清晰的文件——`schema.sql` 只放表、索引与视图；`storage.py` 负责连接、建库、幂等写入、文件水位与项目映射；`pricing.py` 负责价目表读写与成本计算。写入以 `(file_path, ordinal)` 为幂等键，正文不入库（只存长度、状态和「原始文件 + ordinal」指针），成本与缓存命中率不落库。Task 1 是对 P1.1 解析层的三处增量扩展，存储层需要它补出来的轮次起止时间与事件序号。

**Tech Stack:** Python 3.12、`sqlite3`（标准库，不新增依赖）、pydantic v2、pytest、ruff、uv。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（重点 4.2、4.3、5.2、6.3、6.4、9.2、10、11 节）

## Global Constraints

- Python `>=3.12`。运行期依赖仍然只有 `pydantic>=2.7`；存储层只用标准库 `sqlite3`，**不引入任何新的运行期依赖**。
- 数据库默认路径 `~/.agent-lens/agent-lens.db`，可用环境变量 `AGENT_LENS_DB` 覆盖。数据库文件不进仓库（`.gitignore` 已忽略 `*.db`）。
- 连接统一开启 `journal_mode = WAL`、`foreign_keys = ON`、`busy_timeout = 5000`；单写者模式，进程级互斥由调用方（P1.3 采集器）保证。
- 时间一律存 **UTC ISO8601 文本**（`2026-09-27T13:00:00.123456+00:00`），字典序即时间序。
- 幂等键统一为 `(file_path, ordinal)`；重复写入不新增行。`turns` 的主键是 `(session_id, turn_id)`，因为一个会话可能横跨多个文件（设计文档 4.3 节）。
- **正文不入库**：工具参数与工具输出只存长度；`events` 只存事件类型与 payload 的键名。需要原文时按「原始文件 + ordinal」回 JSONL 取。
- **成本与缓存命中率不落库**，一律查询时计算（设计文档 6.4 节）。
- 存储层禁止 import `requests`、`langfuse`，禁止网络请求。
- 标识符用英文，注释与 docstring 用中文。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/agent_lens/models.py` | 追加 `epoch_to_utc`（Task 1） |
| `src/agent_lens/parser.py` | 补齐轮次起止时间、`task_started` 落成 turn、事件带 ordinal（Task 1） |
| `src/agent_lens/schema.sql` | 全部表、索引、视图（Task 2） |
| `src/agent_lens/storage.py` | 连接与建库（Task 2）、幂等写入（Task 3）、水位表（Task 4）、项目归属（Task 5） |
| `src/agent_lens/pricing.py` | 价目表读写与成本计算（Task 6） |
| `tests/conftest.py` | 追加 `lens_db` fixture（Task 2） |
| `tests/test_parser_turns.py` | 解析层扩展的测试（Task 1） |
| `tests/test_storage_schema.py` | schema 与版本（Task 2） |
| `tests/test_storage_write.py` | 幂等写入与「正文不入库」（Task 3） |
| `tests/test_storage_ingest_state.py` | 水位表（Task 4） |
| `tests/test_storage_projects.py` | 项目归属（Task 5） |
| `tests/test_pricing.py` | 价目表与成本（Task 6） |
| `tests/test_storage_replay.py` | 真实 fixture 端到端重放（Task 7） |

执行顺序即编号顺序。Task 3 到 Task 5 都是往 `storage.py` 末尾追加，互不冲突。

---

### Task 1: 解析层的三处增量扩展

**为什么改 P1.1 的代码：** 存储层要落「轮次起止时间」（设计文档 6.3 节的 `turns`），但 P1.1 的 `TurnRecord` 只填了 `duration_ms`；`events` 表要用 `(file_path, ordinal)` 做幂等键，但 P1.1 的事件字典里没有 ordinal。两处都是**只增字段**的向后兼容扩展，Task 7 会用 P1.1 的 43 个测试确认没有回归。

**Files:**

- Modify: `src/agent_lens/models.py`
- Modify: `src/agent_lens/parser.py`
- Create: `tests/test_parser_turns.py`

**Interfaces:**

- Consumes: 无（改的是 P1.1 已有代码）
- Produces:
  - `epoch_to_utc(value: int | float | None) -> datetime | None`
  - `LineParseResult.turn_started: TurnRecord | None`
  - `TurnRecord.started_at` / `completed_at` 在 `task_complete`、`turn_aborted` 上有值
  - `ParsedSession.events[i]["ordinal"]` 为该事件所在行的 ordinal

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_parser_turns.py`：

```python
import json
from datetime import datetime, timezone

from agent_lens.parser import parse_line

UTC = timezone.utc


def test_task_complete_fills_turn_window():
    raw = json.dumps(
        {
            "ordinal": 9,
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "t1",
                "started_at": 1758000000,
                "completed_at": 1758000300,
                "duration_ms": 300000,
            },
        }
    )

    turn = parse_line(raw, "f.jsonl", 9).turn_completed

    assert turn.started_at == datetime.fromtimestamp(1758000000, tz=UTC)
    assert turn.completed_at == datetime.fromtimestamp(1758000300, tz=UTC)


def test_task_started_becomes_turn_started():
    raw = json.dumps(
        {
            "ordinal": 1,
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "t1", "started_at": 1758000000},
        }
    )

    result = parse_line(raw, "f.jsonl", 1)

    assert result.turn_started.turn_id == "t1"
    assert result.turn_started.started_at == datetime.fromtimestamp(1758000000, tz=UTC)
    assert result.is_empty() is False


def test_unknown_event_carries_ordinal():
    raw = json.dumps(
        {"ordinal": 12, "type": "event_msg", "payload": {"type": "context_compacted"}}
    )

    result = parse_line(raw, "f.jsonl", 12)

    assert result.event["ordinal"] == 12
    assert result.event["event_type"] == "context_compacted"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_turns.py -v`

Expected: FAIL，`AttributeError: 'NoneType' object has no attribute 'turn_started'`（`task_started` 目前走事件分支）

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/models.py` 的 `to_utc` 之后追加：

```python
def epoch_to_utc(value: int | float | None) -> datetime | None:
    """把 Unix 秒级时间戳归一化为 UTC aware datetime。

    codex 的 task_started / task_complete 用秒级 epoch 记录轮次起止时间；
    缺字段、类型不对或数值不可解析时返回 None，不猜。
    """
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
```

在 `src/agent_lens/parser.py` 里做四处修改。

第一处，把 `epoch_to_utc` 加进 `.models` 的引入列表（其余名字保持原样）：

```python
from .models import (
    ApiCallRecord,
    ItemCompletedRecord,
    ParsedSession,
    ParseError,
    SessionMetaRecord,
    TokenUsage,
    ToolCallRecord,
    ToolResultRecord,
    TurnContextRecord,
    TurnRecord,
    VerificationResult,
    epoch_to_utc,
    to_utc,
)
```

第二处，`LineParseResult` 增加 `turn_started` 字段，并把它算进 `is_empty()`：

```python
    item_completed: ItemCompletedRecord | None = None
    turn_started: TurnRecord | None = None
    turn_completed: TurnRecord | None = None
    turn_aborted: TurnRecord | None = None
```

```python
                "item_completed",
                "turn_started",
                "turn_completed",
                "turn_aborted",
```

第三处，改写 `_dispatch_event` 里的 `task_complete` 分支、新增 `task_started` 分支、并让 `turn_aborted` 也带起止时间：

```python
    if event_type == "task_complete":
        return LineParseResult(
            turn_completed=TurnRecord(
                turn_id=str(payload.get("turn_id", "")),
                started_at=epoch_to_utc(payload.get("started_at")),
                completed_at=epoch_to_utc(payload.get("completed_at")),
                duration_ms=payload.get("duration_ms"),
            )
        )

    if event_type == "task_started":
        return LineParseResult(
            turn_started=TurnRecord(
                turn_id=str(payload.get("turn_id", "")),
                started_at=epoch_to_utc(payload.get("started_at")),
            )
        )

    if event_type == "turn_aborted":
        return LineParseResult(
            turn_aborted=TurnRecord(
                turn_id=str(payload.get("turn_id", "")),
                started_at=epoch_to_utc(payload.get("started_at")),
                completed_at=epoch_to_utc(payload.get("completed_at")),
                duration_ms=payload.get("duration_ms"),
                aborted_reason=payload.get("reason"),
            )
        )

    return LineParseResult(
        event={"ordinal": ordinal, "event_type": event_type, "payload": payload}
    )
```

第四处，`_merge` 里把 `turn_started` 一起合并：

```python
    for turn in (result.turn_started, result.turn_completed, result.turn_aborted):
        if turn is not None:
            _upsert_turn(parsed, turn)
```

- [ ] **Step 4: 跑测试确认通过（含 P1.1 全量回归）**

Run: `uv run pytest tests/test_parser_turns.py -v`

Expected: PASS，3 passed

Run: `uv run pytest -q`

Expected: PASS，46 passed（P1.1 原有 43 + 新增 3）

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/models.py src/agent_lens/parser.py tests/test_parser_turns.py
git commit -m "feat: expose turn window and event ordinal from codex logs"
```

---

### Task 2: schema、连接与建库

**Files:**

- Create: `src/agent_lens/schema.sql`
- Create: `src/agent_lens/storage.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_storage_schema.py`

**Interfaces:**

- Consumes: 无
- Produces:
  - 常量 `SCHEMA_VERSION = 1`、`SCHEMA_PATH`、`DEFAULT_DB_PATH`、`DB_PATH_ENV = "AGENT_LENS_DB"`、`UNCLASSIFIED_PROJECT = "未归类"`、`COUNTS_TABLES`、`WRITE_TABLES`
  - `utc_now() -> datetime`、`to_iso(value: datetime) -> str`
  - `resolve_db_path(db_path: str | Path | None = None) -> Path`
  - `connect(db_path: str | Path | None = None) -> sqlite3.Connection`
  - `init_db(conn: sqlite3.Connection) -> None`

- [ ] **Step 1: 写失败的测试**

在 `tests/conftest.py` 末尾追加 fixture（保留文件里已有的 `write_jsonl`）：

```python
from agent_lens.storage import connect, init_db


@pytest.fixture
def lens_db(tmp_path: Path):
    """建一个初始化好的临时数据库连接。"""
    conn = connect(tmp_path / "lens.db")
    init_db(conn)
    yield conn
    conn.close()
```

注意：`from agent_lens.storage import ...` 要放在文件顶部与其他 import 一起，不要在中间重复 import。

创建 `tests/test_storage_schema.py`：

```python
from pathlib import Path

from agent_lens.storage import DB_PATH_ENV, connect, init_db, resolve_db_path


def test_schema_is_idempotent_and_versioned(tmp_path: Path):
    conn = connect(tmp_path / "lens.db")
    init_db(conn)
    init_db(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }

    assert {
        "sessions",
        "turns",
        "api_calls",
        "tool_calls",
        "tool_results",
        "items",
        "events",
        "pricing",
        "projects",
        "project_paths",
        "ingest_state",
    } <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_db_path_precedence(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "from_env.db"))

    assert resolve_db_path() == tmp_path / "from_env.db"
    assert resolve_db_path(tmp_path / "explicit.db") == tmp_path / "explicit.db"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_storage_schema.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'agent_lens.storage'`

- [ ] **Step 3: 写最小实现**

创建 `src/agent_lens/schema.sql`（完整内容）：

```sql
-- agent-lens SQLite schema v1
--
-- 约定
--   * 幂等键统一为 (file_path, ordinal)：同一行日志重复写入不会产生新行
--   * 时间一律存 UTC ISO8601 文本（形如 2026-09-27T13:00:00.123456+00:00），字典序即时间序
--   * 正文不入库：只存长度、状态与「原始文件 + ordinal」指针，需要原文时回 JSONL 取
--   * 成本、缓存命中率是派生值，不落库，查询时由 pricing.py 计算

CREATE TABLE IF NOT EXISTS sessions (
    session_id              TEXT PRIMARY KEY,
    cli_version             TEXT,
    cwd                     TEXT,
    model_provider          TEXT,
    base_instructions_chars INTEGER NOT NULL DEFAULT 0,
    recorded_at             TEXT,
    project                 TEXT NOT NULL DEFAULT '未归类',
    first_seen_at           TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_state (
    file_path         TEXT PRIMARY KEY,
    session_id        TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    cli_version       TEXT,
    last_ordinal      INTEGER NOT NULL DEFAULT -1,
    byte_offset       INTEGER NOT NULL DEFAULT 0,
    file_size         INTEGER NOT NULL DEFAULT 0,
    mtime             REAL NOT NULL DEFAULT 0,
    parse_error_count INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    session_id     TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    turn_id        TEXT NOT NULL,
    cwd            TEXT,
    model          TEXT,
    effort         TEXT,
    started_at     TEXT,
    completed_at   TEXT,
    duration_ms    INTEGER,
    aborted_reason TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_id)
);

CREATE TABLE IF NOT EXISTS api_calls (
    file_path                TEXT NOT NULL,
    ordinal                  INTEGER NOT NULL,
    session_id               TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    turn_id                  TEXT,
    response_id              TEXT,
    timestamp                TEXT,
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_write_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    reasoning_output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    file_path       TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    session_id      TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    turn_id         TEXT,
    call_id         TEXT,
    name            TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'function_call',
    arguments_chars INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS tool_results (
    file_path         TEXT NOT NULL,
    ordinal           INTEGER NOT NULL,
    session_id        TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    call_id           TEXT,
    output_chars      INTEGER NOT NULL DEFAULT 0,
    exit_code         INTEGER,
    wall_time_seconds REAL,
    success           INTEGER,
    result_summary    TEXT,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS items (
    file_path       TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    session_id      TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    item_type       TEXT NOT NULL,
    started_at_ms   INTEGER NOT NULL,
    completed_at_ms INTEGER NOT NULL,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS events (
    file_path    TEXT NOT NULL,
    ordinal      INTEGER NOT NULL,
    session_id   TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    event_type   TEXT,
    payload_keys TEXT,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS projects (
    name       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_paths (
    path_prefix  TEXT PRIMARY KEY,
    project_name TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing (
    provider                        TEXT NOT NULL,
    model                           TEXT NOT NULL,
    effective_from                  TEXT NOT NULL,
    input_price_per_mtok            REAL NOT NULL,
    cached_input_price_per_mtok     REAL NOT NULL,
    output_price_per_mtok           REAL NOT NULL,
    reasoning_output_price_per_mtok REAL,
    currency                        TEXT NOT NULL DEFAULT 'USD',
    PRIMARY KEY (provider, model, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_api_calls_session ON api_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_timestamp ON api_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_lookup ON tool_calls(file_path, call_id);
CREATE INDEX IF NOT EXISTS idx_tool_results_lookup ON tool_results(file_path, call_id);
CREATE INDEX IF NOT EXISTS idx_items_session ON items(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_pricing_lookup ON pricing(provider, model, effective_from DESC);

CREATE VIEW IF NOT EXISTS api_call_view AS
SELECT
    a.file_path,
    a.ordinal,
    a.session_id,
    a.turn_id,
    a.response_id,
    a.timestamp,
    a.input_tokens,
    a.cached_input_tokens,
    a.cache_write_input_tokens,
    a.output_tokens,
    a.reasoning_output_tokens,
    a.total_tokens,
    s.project,
    s.cli_version,
    t.model,
    t.effort,
    CAST(a.cached_input_tokens AS REAL) / NULLIF(a.input_tokens, 0) AS cache_hit_rate
FROM api_calls a
LEFT JOIN sessions s ON s.session_id = a.session_id
LEFT JOIN turns t ON t.session_id = a.session_id AND t.turn_id = a.turn_id;

CREATE VIEW IF NOT EXISTS tool_call_details AS
SELECT
    c.file_path,
    c.ordinal AS call_ordinal,
    c.session_id,
    c.turn_id,
    c.call_id,
    c.name,
    c.kind,
    c.arguments_chars,
    r.ordinal AS result_ordinal,
    r.output_chars,
    r.exit_code,
    r.wall_time_seconds,
    r.success,
    r.result_summary
FROM tool_calls c
LEFT JOIN tool_results r ON r.file_path = c.file_path AND r.call_id = c.call_id;
```

创建 `src/agent_lens/storage.py`：

```python
"""SQLite 存储层：schema 初始化、幂等写入、文件水位与项目归属。

设计约定（设计文档 5.2 / 6.3 / 9.2 / 10 节）：
  * 幂等键统一为 (file_path, ordinal)，写入全部走 INSERT OR IGNORE / UPSERT
  * 单写者：本模块只给连接与写入函数，进程级互斥由调用方（P1.3 采集器）保证
  * 正文不入库：只存长度、状态与「原始文件 + ordinal」指针；
    result_summary 预留给 P1.3 脱敏后的摘要，本层不写入
  * 成本与缓存命中率是派生值，查询时由 pricing.py / 视图计算，不落库
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path.home() / ".agent-lens" / "agent-lens.db"
DB_PATH_ENV = "AGENT_LENS_DB"
UNCLASSIFIED_PROJECT = "未归类"
COUNTS_TABLES = (
    "sessions",
    "ingest_state",
    "turns",
    "api_calls",
    "tool_calls",
    "tool_results",
    "items",
    "events",
    "projects",
    "project_paths",
    "pricing",
)
WRITE_TABLES = (
    "sessions",
    "turns",
    "api_calls",
    "tool_calls",
    "tool_results",
    "items",
    "events",
    "ingest_state",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """统一成 UTC ISO8601 文本，保证字典序等于时间序。"""
    return value.astimezone(timezone.utc).isoformat()


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """按 参数 > 环境变量 > 默认路径 的顺序决定数据库位置。"""
    if db_path is not None:
        return Path(db_path).expanduser()
    from_env = os.environ.get(DB_PATH_ENV)
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_DB_PATH


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """打开数据库并把连接调成分析场景需要的状态。"""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表建视图并写入 schema 版本号，可重复执行。"""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本 {current} 高于本代码支持的 {SCHEMA_VERSION}，请升级 agent-lens"
        )
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_storage_schema.py -v`

Expected: PASS，2 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/schema.sql src/agent_lens/storage.py tests/conftest.py tests/test_storage_schema.py
git commit -m "feat: add sqlite schema and connection helpers"
```

---

### Task 3: 幂等写入

**Files:**

- Modify: `src/agent_lens/storage.py`
- Create: `tests/test_storage_write.py`

**Interfaces:**

- Consumes: `ParsedSession` 及其记录模型（P1.1）、`connect` / `init_db` / `to_iso` / `utc_now`（Task 2）
- Produces:
  - `WriteResult`（字段 `session_id`、`file_path`、`inserted: dict[str, int]`、`skipped: dict[str, int]`）
  - `write_parsed_session(conn, parsed, *, project: str | None = None, now: datetime | None = None) -> WriteResult`
  - `counts(conn) -> dict[str, int]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_storage_write.py`：

```python
import pytest

from agent_lens.models import (
    ApiCallRecord,
    ItemCompletedRecord,
    ParsedSession,
    TokenUsage,
    ToolCallRecord,
    ToolResultRecord,
    TurnContextRecord,
    TurnRecord,
    to_utc,
)
from agent_lens.storage import UNCLASSIFIED_PROJECT, counts, write_parsed_session


def minimal_session(file_path: str = "f.jsonl") -> ParsedSession:
    return ParsedSession(
        session_id="s1",
        file_path=file_path,
        cli_version="0.157.1",
        cwd="/Users/someone/project",
        model_provider="deepseek",
        base_instructions_chars=17766,
        recorded_at=to_utc("2026-09-27T10:00:00Z"),
        turn_contexts=[
            TurnContextRecord(turn_id="t1", model="deepseek-flash", effort="high")
        ],
        turns=[TurnRecord(turn_id="t1", duration_ms=3000, aborted_reason=None)],
        api_calls=[
            ApiCallRecord(
                file_path=file_path,
                ordinal=16,
                session_id="s1",
                turn_id="t1",
                response_id="r1",
                timestamp=to_utc("2026-09-27T10:00:01Z"),
                usage=TokenUsage(input_tokens=100, cached_input_tokens=50, output_tokens=10),
                thread_input_tokens_cumulative=100,
            )
        ],
        tool_calls=[
            ToolCallRecord(
                file_path=file_path,
                ordinal=17,
                call_id="c1",
                name="exec_command",
                arguments_raw='{"cmd": "SECRET_ARGUMENT"}',
            )
        ],
        tool_results=[
            ToolResultRecord(
                file_path=file_path,
                ordinal=18,
                call_id="c1",
                output_text="SECRET_OUTPUT",
                exit_code=1,
                wall_time_seconds=2.5,
                success=False,
            )
        ],
        items=[
            ItemCompletedRecord(
                file_path=file_path,
                ordinal=19,
                item_type="CommandExecution",
                started_at_ms=1000,
                completed_at_ms=4500,
            )
        ],
        events=[
            {
                "ordinal": 20,
                "event_type": "context_compacted",
                "payload": {"note": "SECRET_PAYLOAD_VALUE"},
            }
        ],
    )


def test_write_then_rewrite_is_idempotent(lens_db):
    parsed = minimal_session()

    first = write_parsed_session(lens_db, parsed)
    after_first = counts(lens_db)
    second = write_parsed_session(lens_db, parsed)
    after_second = counts(lens_db)

    assert first.inserted["api_calls"] == 1
    assert second.inserted["api_calls"] == 0
    assert second.skipped["api_calls"] == 1
    assert after_first == after_second
    assert after_first["api_calls"] == 1
    assert after_first["tool_calls"] == 1
    assert after_first["tool_results"] == 1
    assert after_first["items"] == 1
    assert after_first["events"] == 1
    assert after_first["turns"] == 1


def test_api_call_view_exposes_cache_hit_rate(lens_db):
    write_parsed_session(lens_db, minimal_session())

    row = lens_db.execute("SELECT * FROM api_call_view").fetchone()

    assert row["project"] == UNCLASSIFIED_PROJECT
    assert row["model"] == "deepseek-flash"
    assert row["cache_hit_rate"] == pytest.approx(50 / 100)


def test_body_text_never_reaches_the_database(lens_db):
    write_parsed_session(lens_db, minimal_session())

    dump = "\n".join(
        str(tuple(row))
        for table in ("api_calls", "tool_calls", "tool_results", "items", "events")
        for row in lens_db.execute(f"SELECT * FROM {table}")
    )

    assert "SECRET_OUTPUT" not in dump
    assert "SECRET_ARGUMENT" not in dump
    assert "SECRET_PAYLOAD_VALUE" not in dump
    # 事件只存键名不存值，键名本身允许出现在库里
    assert '"note"' in dump
    assert lens_db.execute("SELECT arguments_chars FROM tool_calls").fetchone()[0] == len(
        '{"cmd": "SECRET_ARGUMENT"}'
    )
    assert lens_db.execute("SELECT output_chars FROM tool_results").fetchone()[0] == 13


def test_tool_call_details_view_joins_call_and_result(lens_db):
    write_parsed_session(lens_db, minimal_session())

    row = lens_db.execute("SELECT * FROM tool_call_details").fetchone()

    assert row["name"] == "exec_command"
    assert row["exit_code"] == 1
    assert row["success"] == 0
    assert row["wall_time_seconds"] == pytest.approx(2.5)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_storage_write.py -v`

Expected: FAIL，`ImportError: cannot import name 'write_parsed_session'`

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/storage.py` 的 import 区把开头改成下面这样（新增 `json`、`defaultdict`、`BaseModel`、`ParsedSession`）：

```python
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .models import ParsedSession
```

在文件末尾追加：

```python
class WriteResult(BaseModel):
    """一次 write_parsed_session 的结果。

    inserted / skipped 按表给全零值，方便调用方直接读；UPSERT 的表（sessions、turns）
    只在 inserted 里记数，幂等性由主键保证——重复写入不会新增行。
    """

    session_id: str
    file_path: str
    inserted: dict[str, int] = Field(default_factory=dict)
    skipped: dict[str, int] = Field(default_factory=dict)


def _tally(cursor: sqlite3.Cursor, inserted: dict[str, int], skipped: dict[str, int], table: str) -> None:
    if cursor.rowcount:
        inserted[table] += 1
    else:
        skipped[table] += 1


def _upsert_session(conn: sqlite3.Connection, parsed: ParsedSession, project: str, now_iso: str) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, cli_version, cwd, model_provider,
            base_instructions_chars, recorded_at, project, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            cli_version = COALESCE(excluded.cli_version, sessions.cli_version),
            cwd = COALESCE(excluded.cwd, sessions.cwd),
            model_provider = COALESCE(excluded.model_provider, sessions.model_provider),
            base_instructions_chars = MAX(
                sessions.base_instructions_chars, excluded.base_instructions_chars
            ),
            recorded_at = COALESCE(sessions.recorded_at, excluded.recorded_at),
            updated_at = excluded.updated_at
        """,
        (
            parsed.session_id,
            parsed.cli_version,
            parsed.cwd,
            parsed.model_provider,
            parsed.base_instructions_chars,
            to_iso(parsed.recorded_at) if parsed.recorded_at else None,
            project,
            now_iso,
            now_iso,
        ),
    )


def _upsert_turns(conn: sqlite3.Connection, parsed: ParsedSession, now_iso: str) -> int:
    """把 turn_context 与 turn 边界合并成一行。

    同一个 turn 可能出现在同一会话的两个文件里（文件滚动），所以主键是
    (session_id, turn_id)，冲突时做字段级合并而不是覆盖。
    """
    merged: dict[str, dict[str, object]] = {}
    for context in parsed.turn_contexts:
        if not context.turn_id:
            continue
        row = merged.setdefault(context.turn_id, {})
        row["cwd"] = context.cwd
        row["model"] = context.model
        row["effort"] = context.effort
    for turn in parsed.turns:
        if not turn.turn_id:
            continue
        row = merged.setdefault(turn.turn_id, {})
        row["started_at"] = to_iso(turn.started_at) if turn.started_at else None
        row["completed_at"] = to_iso(turn.completed_at) if turn.completed_at else None
        row["duration_ms"] = turn.duration_ms
        row["aborted_reason"] = turn.aborted_reason

    for turn_id, row in merged.items():
        conn.execute(
            """
            INSERT INTO turns (
                session_id, turn_id, cwd, model, effort,
                started_at, completed_at, duration_ms, aborted_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, turn_id) DO UPDATE SET
                cwd = COALESCE(excluded.cwd, turns.cwd),
                model = COALESCE(excluded.model, turns.model),
                effort = COALESCE(excluded.effort, turns.effort),
                started_at = COALESCE(excluded.started_at, turns.started_at),
                completed_at = COALESCE(excluded.completed_at, turns.completed_at),
                duration_ms = COALESCE(excluded.duration_ms, turns.duration_ms),
                aborted_reason = COALESCE(excluded.aborted_reason, turns.aborted_reason),
                updated_at = excluded.updated_at
            """,
            (
                parsed.session_id,
                turn_id,
                row.get("cwd"),
                row.get("model"),
                row.get("effort"),
                row.get("started_at"),
                row.get("completed_at"),
                row.get("duration_ms"),
                row.get("aborted_reason"),
                now_iso,
            ),
        )
    return len(merged)


def _max_ordinal(parsed: ParsedSession) -> int:
    ordinals = [call.ordinal for call in parsed.api_calls]
    ordinals += [call.ordinal for call in parsed.tool_calls]
    ordinals += [result.ordinal for result in parsed.tool_results]
    ordinals += [item.ordinal for item in parsed.items]
    ordinals += [error.ordinal for error in parsed.parse_errors]
    return max(ordinals, default=-1)


def _touch_ingest_state(conn: sqlite3.Connection, parsed: ParsedSession, now_iso: str) -> None:
    conn.execute(
        """
        INSERT INTO ingest_state (
            file_path, session_id, cli_version, last_ordinal, parse_error_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            session_id = COALESCE(excluded.session_id, ingest_state.session_id),
            cli_version = COALESCE(excluded.cli_version, ingest_state.cli_version),
            last_ordinal = MAX(ingest_state.last_ordinal, excluded.last_ordinal),
            parse_error_count = MAX(
                ingest_state.parse_error_count, excluded.parse_error_count
            ),
            updated_at = excluded.updated_at
        """,
        (
            parsed.file_path,
            parsed.session_id,
            parsed.cli_version,
            _max_ordinal(parsed),
            len(parsed.parse_errors),
            now_iso,
        ),
    )
```

继续在末尾追加写入主函数与计数函数：

```python
def write_parsed_session(
    conn: sqlite3.Connection,
    parsed: ParsedSession,
    *,
    project: str | None = None,
    now: datetime | None = None,
) -> WriteResult:
    """把一个 ParsedSession 幂等写库。

    project 为空时按 cwd 走手动映射（Task 5 落地）；命不中就是「未归类」。
    整体在一个事务里，任何一步抛错都不会留下半份数据。
    """
    now_iso = to_iso(now or utc_now())
    resolved_project = project or UNCLASSIFIED_PROJECT
    inserted: dict[str, int] = defaultdict(int)
    skipped: dict[str, int] = defaultdict(int)

    with conn:
        _upsert_session(conn, parsed, resolved_project, now_iso)
        inserted["sessions"] += 1
        inserted["turns"] += _upsert_turns(conn, parsed, now_iso)

        for call in parsed.api_calls:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO api_calls (
                    file_path, ordinal, session_id, turn_id, response_id, timestamp,
                    input_tokens, cached_input_tokens, cache_write_input_tokens,
                    output_tokens, reasoning_output_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.file_path,
                    call.ordinal,
                    call.session_id or parsed.session_id,
                    call.turn_id,
                    call.response_id,
                    to_iso(call.timestamp) if call.timestamp else None,
                    call.usage.input_tokens,
                    call.usage.cached_input_tokens,
                    call.usage.cache_write_input_tokens,
                    call.usage.output_tokens,
                    call.usage.reasoning_output_tokens,
                    call.usage.total_tokens,
                ),
            )
            _tally(cursor, inserted, skipped, "api_calls")

        for tool_call in parsed.tool_calls:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO tool_calls (
                    file_path, ordinal, session_id, call_id, name, kind, arguments_chars
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call.file_path,
                    tool_call.ordinal,
                    parsed.session_id,
                    tool_call.call_id,
                    tool_call.name,
                    tool_call.kind,
                    len(tool_call.arguments_raw),
                ),
            )
            _tally(cursor, inserted, skipped, "tool_calls")

        for tool_result in parsed.tool_results:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO tool_results (
                    file_path, ordinal, session_id, call_id,
                    output_chars, exit_code, wall_time_seconds, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_result.file_path,
                    tool_result.ordinal,
                    parsed.session_id,
                    tool_result.call_id,
                    len(tool_result.output_text),
                    tool_result.exit_code,
                    tool_result.wall_time_seconds,
                    None if tool_result.success is None else int(tool_result.success),
                ),
            )
            _tally(cursor, inserted, skipped, "tool_results")

        for item in parsed.items:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO items (
                    file_path, ordinal, session_id, item_type, started_at_ms, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.file_path,
                    item.ordinal,
                    parsed.session_id,
                    item.item_type,
                    item.started_at_ms,
                    item.completed_at_ms,
                ),
            )
            _tally(cursor, inserted, skipped, "items")

        for index, event in enumerate(parsed.events):
            payload = event.get("payload")
            payload_keys = sorted(payload) if isinstance(payload, dict) else []
            ordinal = event.get("ordinal")
            if not isinstance(ordinal, int):
                ordinal = -1 - index
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    file_path, ordinal, session_id, event_type, payload_keys
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    parsed.file_path,
                    ordinal,
                    parsed.session_id,
                    event.get("event_type"),
                    json.dumps(payload_keys, ensure_ascii=False),
                ),
            )
            _tally(cursor, inserted, skipped, "events")

        _touch_ingest_state(conn, parsed, now_iso)
        inserted["ingest_state"] += 1

    return WriteResult(
        session_id=parsed.session_id,
        file_path=parsed.file_path,
        inserted={table: inserted.get(table, 0) for table in WRITE_TABLES},
        skipped={table: skipped.get(table, 0) for table in WRITE_TABLES},
    )


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """各表行数，用于幂等性测试与后续总览页。"""
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in COUNTS_TABLES
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_storage_write.py -v`

Expected: PASS，4 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/storage.py tests/test_storage_write.py
git commit -m "feat: write parsed sessions into sqlite idempotently"
```

---

### Task 4: 文件水位表

**Files:**

- Modify: `src/agent_lens/storage.py`
- Create: `tests/test_storage_ingest_state.py`

**Interfaces:**

- Consumes: `write_parsed_session`（Task 3）
- Produces:
  - `IngestState`（字段 `file_path`、`session_id`、`cli_version`、`last_ordinal`、`byte_offset`、`file_size`、`mtime`、`parse_error_count`）
  - `get_ingest_state(conn, file_path: str) -> IngestState | None`
  - `update_ingest_state(conn, *, file_path: str, session_id=None, cli_version=None, last_ordinal=None, byte_offset=None, file_size=None, mtime=None, parse_error_count=None, now=None) -> None`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_storage_ingest_state.py`：

```python
import pytest

from agent_lens.storage import get_ingest_state, update_ingest_state, write_parsed_session
from tests.test_storage_write import minimal_session


def test_ingest_state_tracks_watermark(lens_db):
    write_parsed_session(lens_db, minimal_session("f.jsonl"))
    update_ingest_state(
        lens_db,
        file_path="f.jsonl",
        byte_offset=4096,
        file_size=8192,
        mtime=123.5,
        last_ordinal=20,
    )

    state = get_ingest_state(lens_db, "f.jsonl")

    assert state.last_ordinal == 20
    assert state.byte_offset == 4096
    assert state.file_size == 8192
    assert state.mtime == pytest.approx(123.5)
    assert state.cli_version == "0.157.1"


def test_watermark_never_goes_backwards(lens_db):
    update_ingest_state(lens_db, file_path="f.jsonl", last_ordinal=50)
    update_ingest_state(lens_db, file_path="f.jsonl", last_ordinal=30)

    assert get_ingest_state(lens_db, "f.jsonl").last_ordinal == 50


def test_unknown_file_has_no_state(lens_db):
    assert get_ingest_state(lens_db, "never-seen.jsonl") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_storage_ingest_state.py -v`

Expected: FAIL，`ImportError: cannot import name 'get_ingest_state'`

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/storage.py` 末尾追加：

```python
class IngestState(BaseModel):
    """一个日志文件的读取水位。"""

    file_path: str
    session_id: str | None = None
    cli_version: str | None = None
    last_ordinal: int = -1
    byte_offset: int = 0
    file_size: int = 0
    mtime: float = 0.0
    parse_error_count: int = 0


def get_ingest_state(conn: sqlite3.Connection, file_path: str) -> IngestState | None:
    row = conn.execute("SELECT * FROM ingest_state WHERE file_path = ?", (file_path,)).fetchone()
    if row is None:
        return None
    return IngestState(
        file_path=row["file_path"],
        session_id=row["session_id"],
        cli_version=row["cli_version"],
        last_ordinal=row["last_ordinal"],
        byte_offset=row["byte_offset"],
        file_size=row["file_size"],
        mtime=row["mtime"],
        parse_error_count=row["parse_error_count"],
    )


def update_ingest_state(
    conn: sqlite3.Connection,
    *,
    file_path: str,
    session_id: str | None = None,
    cli_version: str | None = None,
    last_ordinal: int | None = None,
    byte_offset: int | None = None,
    file_size: int | None = None,
    mtime: float | None = None,
    parse_error_count: int | None = None,
    now: datetime | None = None,
) -> None:
    """更新一个文件的读取水位。未传入的参数保持原值。

    last_ordinal 只增不减（水位语义）；其余字段按传入值覆盖。
    P1.3 的增量采集器读取新内容后调用这个函数推进 offset。
    """
    now_iso = to_iso(now or utc_now())
    values = {
        "session_id": session_id,
        "cli_version": cli_version,
        "byte_offset": byte_offset,
        "file_size": file_size,
        "mtime": mtime,
        "parse_error_count": parse_error_count,
    }
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO ingest_state (file_path, updated_at) VALUES (?, ?)",
            (file_path, now_iso),
        )
        assignments = [f"{column} = ?" for column, value in values.items() if value is not None]
        params = [value for value in values.values() if value is not None]
        if last_ordinal is not None:
            assignments.append("last_ordinal = MAX(last_ordinal, ?)")
            params.append(last_ordinal)
        assignments.append("updated_at = ?")
        params.extend([now_iso, file_path])
        conn.execute(
            f"UPDATE ingest_state SET {', '.join(assignments)} WHERE file_path = ?",
            params,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_storage_ingest_state.py -v`

Expected: PASS，3 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/storage.py tests/test_storage_ingest_state.py
git commit -m "feat: track per-file ingest watermarks"
```

---

### Task 5: 项目归属

**Files:**

- Modify: `src/agent_lens/storage.py`
- Create: `tests/test_storage_projects.py`

**Interfaces:**

- Consumes: `write_parsed_session`（Task 3）、`init_db`（Task 2）
- Produces:
  - `assign_project(conn, path_prefix: str, project: str, *, now=None) -> None`
  - `resolve_project(conn, cwd: str | None, *, fallback: str = UNCLASSIFIED_PROJECT) -> str`
  - `refresh_session_projects(conn, *, now=None) -> int`

**范围说明：** 本任务只做设计文档 9.2 节的第 1 条（手动映射，优先级最高）与第 3 条（未归类兜底）。第 2 条「从 cwd 向上找 `.git`」需要文件系统 IO，属于 P1.3 采集器的职责，不在本计划内。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_storage_projects.py`：

```python
from agent_lens.storage import (
    UNCLASSIFIED_PROJECT,
    assign_project,
    refresh_session_projects,
    resolve_project,
    write_parsed_session,
)
from tests.test_storage_write import minimal_session


def test_longest_prefix_wins_and_falls_back(lens_db):
    assign_project(lens_db, "/Users/someone/project", "agent-lens")
    assign_project(lens_db, "/Users/someone/project/vendor", "vendor-lib")

    assert resolve_project(lens_db, "/Users/someone/project/app") == "agent-lens"
    assert resolve_project(lens_db, "/Users/someone/project/vendor/lib") == "vendor-lib"
    assert resolve_project(lens_db, "/Users/someone/other") == UNCLASSIFIED_PROJECT
    assert resolve_project(lens_db, None) == UNCLASSIFIED_PROJECT


def test_prefix_matching_ignores_like_wildcards(lens_db):
    assign_project(lens_db, "/Users/someone/pro_ject", "underscore-project")

    assert resolve_project(lens_db, "/Users/someone/proXject/app") == UNCLASSIFIED_PROJECT


def test_refresh_projects_repairs_existing_sessions(lens_db):
    write_parsed_session(lens_db, minimal_session())
    before = lens_db.execute("SELECT project FROM sessions").fetchone()[0]

    assign_project(lens_db, "/Users/someone/project", "agent-lens")
    changed = refresh_session_projects(lens_db)

    assert before == UNCLASSIFIED_PROJECT
    assert changed == 1
    assert lens_db.execute("SELECT project FROM sessions").fetchone()[0] == "agent-lens"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_storage_projects.py -v`

Expected: FAIL，`ImportError: cannot import name 'assign_project'`

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/storage.py` 末尾追加：

```python
def assign_project(
    conn: sqlite3.Connection,
    path_prefix: str,
    project: str,
    *,
    now: datetime | None = None,
) -> None:
    """注册一条手动映射：路径前缀 -> 项目名（设计文档 9.2 节优先级最高）。"""
    now_iso = to_iso(now or utc_now())
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects (name, created_at) VALUES (?, ?)",
            (project, now_iso),
        )
        conn.execute(
            """
            INSERT INTO project_paths (path_prefix, project_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(path_prefix) DO UPDATE SET project_name = excluded.project_name
            """,
            (path_prefix, project, now_iso),
        )


def resolve_project(
    conn: sqlite3.Connection,
    cwd: str | None,
    *,
    fallback: str = UNCLASSIFIED_PROJECT,
) -> str:
    """按最长前缀匹配决定项目归属，命不中返回 fallback。

    用 substr 比较而不是 LIKE，避免 cwd 里的 % 和 _ 被当成通配符。
    v1 大小写敏感；Windows 路径归一化由 P1.3 采集器负责。
    """
    if not cwd:
        return fallback
    row = conn.execute(
        """
        SELECT project_name
        FROM project_paths
        WHERE substr(?, 1, LENGTH(path_prefix)) = path_prefix
        ORDER BY LENGTH(path_prefix) DESC
        LIMIT 1
        """,
        (cwd,),
    ).fetchone()
    return row["project_name"] if row else fallback


def refresh_session_projects(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    """按当前手动映射重算所有会话的项目归属，返回被改动的会话数。

    设置页改完映射后调用（设计文档 9.2 节的「一键指派」）。
    """
    now_iso = to_iso(now or utc_now())
    rows = conn.execute("SELECT session_id, cwd, project FROM sessions").fetchall()
    changed: list[tuple[str, str, str]] = []
    for row in rows:
        project = resolve_project(conn, row["cwd"])
        if project != row["project"]:
            changed.append((project, now_iso, row["session_id"]))
    if changed:
        with conn:
            conn.executemany(
                "UPDATE sessions SET project = ?, updated_at = ? WHERE session_id = ?",
                changed,
            )
    return len(changed)
```

然后回到 Task 3 写的 `write_parsed_session`，把这一行：

```python
    resolved_project = project or UNCLASSIFIED_PROJECT
```

改成：

```python
    resolved_project = project or resolve_project(conn, parsed.cwd)
```

这样写入时会自动套用手动映射；映射在写入之后才建的情况由 `refresh_session_projects` 兜底。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_storage_projects.py -v`

Expected: PASS，3 passed

Run: `uv run pytest -q`

Expected: PASS，58 passed（P1.1 的 43 + Task 1 的 3 + Task 2 到 Task 5 的 12）

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/storage.py tests/test_storage_projects.py
git commit -m "feat: resolve project ownership by manual path mapping"
```

---

### Task 6: 价目表与成本计算

**Files:**

- Create: `src/agent_lens/pricing.py`
- Create: `tests/test_pricing.py`

**Interfaces:**

- Consumes: `TokenUsage`（P1.1）、`api_call_view` 与 `pricing` 表（Task 2）
- Produces:
  - `PRICE_SCALE = 1_000_000`、`DEFAULT_CURRENCY = "USD"`
  - `PriceEntry`（`provider`、`model`、`effective_from`、`input_price_per_mtok`、`cached_input_price_per_mtok`、`output_price_per_mtok`、`reasoning_output_price_per_mtok: float | None`、`currency`）
  - `CostSummary`（`total`、`currency`、`priced_calls`、`unpriced_calls`）
  - `estimate_cost(usage: TokenUsage, price: PriceEntry) -> float`
  - `upsert_price(conn, entry: PriceEntry) -> None`
  - `list_prices(conn, provider=None, model=None) -> list[PriceEntry]`
  - `price_at(conn, provider, model, *, at=None, now=None) -> PriceEntry | None`
  - `summarize_cost(conn, *, provider, model=None, at_call_time=False, now=None) -> CostSummary`

**关于 reasoning 计价：** 设计文档第 13 节的待验证问题 2（deepseek 是否对 reasoning token 单独计价）还没有结论，所以 `pricing` 表额外留一列 `reasoning_output_price_per_mtok`，为空时自动并入 output 价。**计划里的价格数字（0.28 / 0.028 / 0.42）只是测试用的合成值，不是真实账单价**；真实价目表等核对账单后由设置页录入，不影响本任务的验收。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_pricing.py`：

```python
import pytest

from agent_lens.models import TokenUsage, to_utc
from agent_lens.pricing import PriceEntry, estimate_cost, price_at, summarize_cost, upsert_price
from agent_lens.storage import write_parsed_session
from tests.test_storage_write import minimal_session


def price(input_price=1.0, cached=0.1, output=2.0, reasoning=None, when="2026-09-01T00:00:00Z"):
    return PriceEntry(
        provider="deepseek",
        model="deepseek-flash",
        effective_from=to_utc(when),
        input_price_per_mtok=input_price,
        cached_input_price_per_mtok=cached,
        output_price_per_mtok=output,
        reasoning_output_price_per_mtok=reasoning,
    )


def test_cost_splits_cached_and_uncached():
    usage = TokenUsage(input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=100_000)

    cost = estimate_cost(usage, price())

    assert cost == pytest.approx(0.6 * 1.0 + 0.4 * 0.1 + 0.1 * 2.0)


def test_reasoning_tokens_are_not_double_billed():
    usage = TokenUsage(output_tokens=100_000, reasoning_output_tokens=40_000)

    without_own_price = estimate_cost(usage, price())
    with_own_price = estimate_cost(usage, price(reasoning=0.5))

    assert without_own_price == pytest.approx(0.1 * 2.0)
    assert with_own_price == pytest.approx(0.06 * 2.0 + 0.04 * 0.5)


def test_price_lookup_respects_effective_from(lens_db):
    old = price(input_price=1.0, when="2026-08-01T00:00:00Z")
    new = price(input_price=2.0, when="2026-09-20T00:00:00Z")
    upsert_price(lens_db, old)
    upsert_price(lens_db, new)

    assert price_at(lens_db, "deepseek", "deepseek-flash", at=to_utc("2026-09-10T00:00:00Z")) == old
    assert price_at(lens_db, "deepseek", "deepseek-flash", at=to_utc("2026-09-25T00:00:00Z")) == new
    assert price_at(lens_db, "deepseek", "deepseek-flash", at=to_utc("2026-07-01T00:00:00Z")) is None


def test_upsert_price_corrects_same_effective_from(lens_db):
    upsert_price(lens_db, price(input_price=1.0))
    upsert_price(lens_db, price(input_price=3.0))

    row = lens_db.execute(
        "SELECT COUNT(*) AS n, MAX(input_price_per_mtok) AS p FROM pricing"
    ).fetchone()

    assert row["n"] == 1
    assert row["p"] == pytest.approx(3.0)


def test_cost_is_recomputed_after_price_change(lens_db):
    write_parsed_session(lens_db, minimal_session())
    upsert_price(lens_db, price(input_price=1.0, cached=1.0, output=0.0))
    before = summarize_cost(lens_db, provider="deepseek", now=to_utc("2026-09-27T12:00:00Z"))
    upsert_price(lens_db, price(input_price=2.0, cached=2.0, output=0.0))
    after = summarize_cost(lens_db, provider="deepseek", now=to_utc("2026-09-27T12:00:00Z"))

    assert before.total == pytest.approx(100 * 1.0 / 1_000_000)
    assert after.total == pytest.approx(before.total * 2)
    assert before.priced_calls == 1


def test_historical_pricing_uses_call_time(lens_db):
    parsed = minimal_session()
    parsed.api_calls[0].timestamp = to_utc("2026-08-10T00:00:00Z")
    write_parsed_session(lens_db, parsed)
    upsert_price(lens_db, price(input_price=1.0, cached=1.0, output=0.0, when="2026-08-01T00:00:00Z"))
    upsert_price(lens_db, price(input_price=5.0, cached=5.0, output=0.0, when="2026-09-01T00:00:00Z"))

    current = summarize_cost(
        lens_db, provider="deepseek", at_call_time=False, now=to_utc("2026-09-27T12:00:00Z")
    )
    historical = summarize_cost(
        lens_db, provider="deepseek", at_call_time=True, now=to_utc("2026-09-27T12:00:00Z")
    )

    assert current.total == pytest.approx(100 * 5.0 / 1_000_000)
    assert historical.total == pytest.approx(100 * 1.0 / 1_000_000)


def test_unpriced_calls_are_reported(lens_db):
    write_parsed_session(lens_db, minimal_session())

    summary = summarize_cost(lens_db, provider="deepseek")

    assert summary.priced_calls == 0
    assert summary.unpriced_calls == 1
    assert summary.total == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_pricing.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'agent_lens.pricing'`

- [ ] **Step 3: 写最小实现**

创建 `src/agent_lens/pricing.py`：

```python
"""成本计算：价目表读写与按 token 用量估算费用。

设计约定（设计文档 6.4 节）：
  * 成本不落库，查询时计算，所以改价后历史能一致重算
  * pricing 每条带 effective_from，支持「按当时价目表」与「按当前价目表」两种口径
  * 价格单位统一为「每百万 token 的金额」（price per million tokens）
  * reasoning_output_tokens 已包含在 output_tokens 内（4.4 节实测），
    未单独定价时并入 output 价计算，绝不重复相加
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel

from .models import TokenUsage

PRICE_SCALE = 1_000_000
DEFAULT_CURRENCY = "USD"


class PriceEntry(BaseModel):
    """一个 (provider, model) 在某个生效时刻的价格。"""

    provider: str
    model: str
    effective_from: datetime
    input_price_per_mtok: float
    cached_input_price_per_mtok: float
    output_price_per_mtok: float
    reasoning_output_price_per_mtok: float | None = None
    currency: str = DEFAULT_CURRENCY


class CostSummary(BaseModel):
    """一次成本汇总。unpriced_calls 用来暴露「有调用但查不到价目表」。"""

    total: float
    currency: str = ""
    priced_calls: int = 0
    unpriced_calls: int = 0


def to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def estimate_cost(usage: TokenUsage, price: PriceEntry) -> float:
    """按一次调用的 token 用量与价目表算钱。

    (input - cached) 走未命中价，cached 走命中价，
    output 里的非 reasoning 部分走 output 价，reasoning 部分走它自己的价
    （没配就并入 output 价）。
    """
    reasoning_tokens = usage.reasoning_output_tokens
    plain_output_tokens = usage.output_tokens - reasoning_tokens
    reasoning_price = price.reasoning_output_price_per_mtok
    if reasoning_price is None:
        reasoning_price = price.output_price_per_mtok
    billed = (
        usage.uncached_input_tokens * price.input_price_per_mtok
        + usage.cached_input_tokens * price.cached_input_price_per_mtok
        + plain_output_tokens * price.output_price_per_mtok
        + reasoning_tokens * reasoning_price
    )
    return billed / PRICE_SCALE


def upsert_price(conn: sqlite3.Connection, entry: PriceEntry) -> None:
    """写入一条价目表记录。同一 (provider, model, effective_from) 重复写入即修正。"""
    with conn:
        conn.execute(
            """
            INSERT INTO pricing (
                provider, model, effective_from,
                input_price_per_mtok, cached_input_price_per_mtok,
                output_price_per_mtok, reasoning_output_price_per_mtok, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, model, effective_from) DO UPDATE SET
                input_price_per_mtok = excluded.input_price_per_mtok,
                cached_input_price_per_mtok = excluded.cached_input_price_per_mtok,
                output_price_per_mtok = excluded.output_price_per_mtok,
                reasoning_output_price_per_mtok = excluded.reasoning_output_price_per_mtok,
                currency = excluded.currency
            """,
            (
                entry.provider,
                entry.model,
                to_iso(entry.effective_from),
                entry.input_price_per_mtok,
                entry.cached_input_price_per_mtok,
                entry.output_price_per_mtok,
                entry.reasoning_output_price_per_mtok,
                entry.currency,
            ),
        )


def _row_to_entry(row: sqlite3.Row) -> PriceEntry:
    return PriceEntry(
        provider=row["provider"],
        model=row["model"],
        effective_from=datetime.fromisoformat(row["effective_from"]),
        input_price_per_mtok=row["input_price_per_mtok"],
        cached_input_price_per_mtok=row["cached_input_price_per_mtok"],
        output_price_per_mtok=row["output_price_per_mtok"],
        reasoning_output_price_per_mtok=row["reasoning_output_price_per_mtok"],
        currency=row["currency"],
    )


def list_prices(
    conn: sqlite3.Connection,
    provider: str | None = None,
    model: str | None = None,
) -> list[PriceEntry]:
    sql = "SELECT * FROM pricing"
    params: list[str] = []
    filters = []
    if provider is not None:
        filters.append("provider = ?")
        params.append(provider)
    if model is not None:
        filters.append("model = ?")
        params.append(model)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY provider, model, effective_from"
    return [_row_to_entry(row) for row in conn.execute(sql, params).fetchall()]


def price_at(
    conn: sqlite3.Connection,
    provider: str,
    model: str,
    *,
    at: datetime | None = None,
    now: datetime | None = None,
) -> PriceEntry | None:
    """取生效时间不晚于 at 的最新一条价目表。

    at 为空表示「按当前价目表」，即以 now（默认系统当前时间）为基准。
    """
    moment = at or now or datetime.now(timezone.utc)
    row = conn.execute(
        """
        SELECT * FROM pricing
        WHERE provider = ? AND model = ? AND effective_from <= ?
        ORDER BY effective_from DESC
        LIMIT 1
        """,
        (provider, model, to_iso(moment)),
    ).fetchone()
    return _row_to_entry(row) if row is not None else None


def summarize_cost(
    conn: sqlite3.Connection,
    *,
    provider: str,
    model: str | None = None,
    at_call_time: bool = False,
    now: datetime | None = None,
) -> CostSummary:
    """汇总所有 api_call 的成本。

    model 为空时按每个 turn 自己的模型查价；at_call_time=True 用调用发生时刻的
    价目表（历史精确计价），否则全部用当前价目表重算。
    """
    rows = conn.execute("SELECT * FROM api_call_view ORDER BY timestamp").fetchall()
    total = 0.0
    currency = ""
    priced = 0
    unpriced = 0
    cache: dict[tuple[str, str], PriceEntry | None] = {}

    for row in rows:
        call_model = model or row["model"]
        call_at = None
        if at_call_time:
            if not row["timestamp"]:
                unpriced += 1
                continue
            call_at = datetime.fromisoformat(row["timestamp"])
        if not call_model:
            unpriced += 1
            continue

        cache_key = (call_model, call_at.isoformat() if call_at else "")
        if cache_key not in cache:
            cache[cache_key] = price_at(conn, provider, call_model, at=call_at, now=now)
        price = cache[cache_key]
        if price is None:
            unpriced += 1
            continue

        usage = TokenUsage(
            input_tokens=row["input_tokens"],
            cached_input_tokens=row["cached_input_tokens"],
            cache_write_input_tokens=row["cache_write_input_tokens"],
            output_tokens=row["output_tokens"],
            reasoning_output_tokens=row["reasoning_output_tokens"],
            total_tokens=row["total_tokens"],
        )
        total += estimate_cost(usage, price)
        currency = price.currency
        priced += 1

    return CostSummary(
        total=total, currency=currency, priced_calls=priced, unpriced_calls=unpriced
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_pricing.py -v`

Expected: PASS，7 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/pricing.py tests/test_pricing.py
git commit -m "feat: price api calls at query time"
```

---

### Task 7: 真实 fixture 端到端重放

**Files:**

- Create: `tests/test_storage_replay.py`

**Interfaces:**

- Consumes: `parse_session_file`（P1.1）、`write_parsed_session` / `counts`（Task 3）、`upsert_price` / `summarize_cost`（Task 6）
- Produces: 一条覆盖「真实日志 → 解析 → 入库两次 → 成本重算」的端到端回归测试

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_storage_replay.py`：

```python
import json
from pathlib import Path

import pytest

from agent_lens.models import to_utc
from agent_lens.parser import parse_session_file
from agent_lens.pricing import PriceEntry, summarize_cost, upsert_price
from agent_lens.storage import counts, write_parsed_session

FIXTURE = Path(__file__).parent / "fixtures" / "real_session_sample.jsonl"


def test_replay_real_fixture_is_idempotent_and_priced(lens_db):
    parsed = parse_session_file(FIXTURE)
    meta = json.loads((FIXTURE.parent / "real_session_sample.meta.json").read_text("utf-8"))

    write_parsed_session(lens_db, parsed)
    model = lens_db.execute(
        "SELECT model FROM turns WHERE model IS NOT NULL LIMIT 1"
    ).fetchone()["model"]
    upsert_price(
        lens_db,
        PriceEntry(
            provider="deepseek",
            model=model,
            effective_from=to_utc("2026-01-01T00:00:00Z"),
            input_price_per_mtok=0.28,
            cached_input_price_per_mtok=0.028,
            output_price_per_mtok=0.42,
        ),
    )

    first_counts = counts(lens_db)
    first_cost = summarize_cost(lens_db, provider="deepseek")

    write_parsed_session(lens_db, parsed)

    assert counts(lens_db) == first_counts
    assert summarize_cost(lens_db, provider="deepseek").total == pytest.approx(first_cost.total)
    assert first_counts["api_calls"] == meta["api_call_count"]
    assert first_cost.priced_calls == meta["api_call_count"]
    assert first_cost.unpriced_calls == 0
    assert first_cost.total > 0


def test_replay_records_turn_window_and_tool_join(lens_db):
    parsed = parse_session_file(FIXTURE)

    write_parsed_session(lens_db, parsed)

    turn = lens_db.execute(
        "SELECT * FROM turns WHERE started_at IS NOT NULL LIMIT 1"
    ).fetchone()
    joined = lens_db.execute("SELECT COUNT(*) FROM tool_call_details").fetchone()[0]

    assert turn["started_at"] is not None
    assert turn["completed_at"] is not None
    assert joined == len(parsed.tool_calls)
```

测试里的 0.28 / 0.028 / 0.42 是**合成价格**，只用于验证公式与链路；真实价目表在核对账单后录入。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_storage_replay.py -v`

Expected: 如果 Task 1 到 Task 6 都已完成，这里应当直接 PASS；若有 FAIL，先按错误修上游任务，不要在测试里绕过。

- [ ] **Step 3: 跑全量测试**

Run: `uv run pytest -q`

Expected: PASS，67 passed（P1.1 的 43 + 本计划的 24）

- [ ] **Step 4: 提交**

```bash
git add tests/test_storage_replay.py
git commit -m "test: replay a real session through storage end to end"
```

---

## Self-Review

### Spec coverage

| 设计文档要求 | 覆盖位置 |
|---|---|
| 4.2 累计字段重置、总量对 usage 求和 | 存储层只接受 P1.1 的 `usage` 求和结果；`api_calls` 六项字段逐一落库，`thread_input_tokens_cumulative` 不入库（只作自检） |
| 4.3 一个会话跨多个文件 | `turns` 主键 `(session_id, turn_id)` + 冲突合并；`ingest_state` 按 `file_path` 记录每个文件的水位 |
| 5.2 幂等与断点续传 | Task 3 的 `INSERT OR IGNORE` / UPSERT 与幂等性测试；Task 4 的水位表 |
| 6.3 核心表 | Task 2 的 `schema.sql`（sessions / turns / api_calls / tool_calls / tool_results / items / events / pricing / projects / project_paths / ingest_state） |
| 6.3 正文不入库 | Task 3 的 `arguments_chars` / `output_chars` / `payload_keys` 与 `test_body_text_never_reaches_the_database`；`result_summary` 列留给 P1.3 填脱敏摘要 |
| 6.3 只保留「原始文件 + 行号」指针 | 每张事实表的主键都是 `(file_path, ordinal)` |
| 6.4 成本不落库、可重算、支持历史计价 | Task 6 的 `estimate_cost` / `price_at` / `summarize_cost` 与两个改价测试 |
| 6.4 价目表三档 + 可能的第四档 | `pricing` 表的 `input` / `cached_input` / `output` / 可空的 `reasoning_output` |
| 9.2 项目归属（手动映射 + 兜底） | Task 5；`.git` 向上推断归 P1.3 |
| 10 单写者、WAL、幂等键冲突 | Task 2 的连接设置；Task 3 的全部写入 |
| 11 幂等性测试、成本计算测试 | Task 3、Task 6、Task 7 |

不在本计划范围：`.git` 项目推断、增量扫描与半行回退、批量上报、API 与前端（分别属于 P1.3 与 P1.4）。

### 与设计文档的两处有意偏差

1. **`tool_calls` 与 `tool_results` 拆成两张表**，再用 `tool_call_details` 视图合并。设计文档 6.3 节把「工具名、耗时、成功状态、退出码、结果摘要」列在同一张 `tool_calls` 下，但 P1.1 的解析结果里调用与结果是两条独立的日志行、各有自己的 ordinal。拆开后每张表都能用 `INSERT OR IGNORE` 纯追加，不需要跨行更新；合并查询交给视图。
2. **`events` 只存事件类型与 payload 的键名**，不存 payload 值。原因同上：事件行没有结构化字段，原样入库等于把正文写进数据库。

### Placeholder scan

全文无 TBD、无 TODO、无「类似 Task N」的省略。每个代码步骤都给了完整可运行代码。

### 测试数与可执行性

本计划的全部代码在写入文档前已在临时工作区按 Task 拆分跑通：逐任务为 3 / 2 / 4 / 3 / 3 / 7 / 2 个测试，合计 24 个；与 P1.1 的 43 个合并后 `pytest -q` 为 **67 passed**。计划里每个 Step 4 的期望数字都是实测值，不是估算。

### Type consistency

- `WriteResult.inserted` / `skipped` 的键集固定为 `WRITE_TABLES`，重复写入时不会出现缺键。
- `write_parsed_session` 的 `project` 参数与 `resolve_project` 的返回值都是 `str`，兜底常量统一用 `UNCLASSIFIED_PROJECT`。
- `to_iso` 在 `storage.py` 与 `pricing.py` 各有一份（两个模块都能独立使用）；含义相同，都是 UTC ISO8601 文本。
- `PriceEntry.effective_from` 是 `datetime`，入库统一 `to_iso`，出库统一 `datetime.fromisoformat`，两侧对称。
- `TokenUsage.uncached_input_tokens` 来自 P1.1 的派生属性，成本公式用它而不是重复做减法。
- `IngestState.last_ordinal` 的默认值 `-1` 与 `write_parsed_session` 在空文件上的 `_max_ordinal` 返回值一致。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-09-27-p1-2-storage-layer.md`。两种执行方式：

**1. 子代理驱动（推荐）** —— 每个 Task 派一个全新的子代理去实现，我在 Task 之间审查，上下文干净、迭代快。

**2. 当前会话内执行** —— 用 `superpowers:executing-plans` 在当前会话里成批执行，带检查点。

你选哪种？
