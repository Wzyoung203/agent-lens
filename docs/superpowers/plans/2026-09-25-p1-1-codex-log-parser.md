# P1.1 Codex 日志解析器 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Codex 写在 `~/.codex/sessions/**.jsonl` 里的会话日志，解析成结构化的 Python 对象，供后续存储层使用。

**Architecture:** 纯函数式解析层，零副作用——不写数据库、不发网络请求、不修改源文件。输入是文件路径，输出是 `ParsedSession` 聚合对象。所有容错都在这一层完成，下游拿到的是已经校验过的数据。

**Tech Stack:** Python 3.12、pydantic v2、pytest、ruff、uv。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（重点第 4 节与第 6.1 节）

## Global Constraints

- Python `>=3.12`。
- 运行期依赖只有 `pydantic>=2.7`；开发依赖只有 `pytest>=8.0` 与 `ruff>=0.5`。**不引入 watchdog 或任何文件监听库**（设计文档 5.3 节：用轮询扫描）。
- 解析层**只读**。禁止写入 `~/.codex` 下的任何路径。
- 解析层禁止 import `sqlite3`、`requests`、`langfuse`，禁止发起网络请求。
- 所有时间戳归一化为 **UTC aware** 的 `datetime`。
- 幂等键统一为 `(file_path, ordinal)`。
- 标识符用英文，注释与 docstring 用中文。
- 字段全部按可选处理：源数据存在版本漂移（设计文档 4.6 节，`0.153.4` 的会话完全没有 `token_usage_record`）。
- 正文（工具参数、工具输出）原样保留。脱敏是 P1.3 入库前的职责，本计划不做。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `pyproject.toml` | 项目元信息、依赖、pytest 与 ruff 配置 |
| `src/agent_lens/__init__.py` | 包入口 |
| `src/agent_lens/models.py` | 全部 pydantic 模型与派生属性（纯数据，无 IO） |
| `src/agent_lens/parser.py` | 行解析与文件解析（纯函数） |
| `tests/conftest.py` | 共享 fixture：构造 JSONL 临时文件 |
| `tests/test_models.py` | 模型校验与派生属性 |
| `tests/test_parser_metadata.py` | 工具输出文本的元数据提取 |
| `tests/test_parser_line.py` | 单行分派 |
| `tests/test_parser_session.py` | 整文件解析与边界情况 |
| `tests/test_verification.py` | 累计字段自检 |
| `scripts/make_fixture.py` | 从真实日志裁剪出脱敏的测试 fixture |

---

### Task 1: 项目骨架

**Files:**

- Create: `pyproject.toml`
- Create: `src/agent_lens/__init__.py`
- Create: `tests/test_smoke.py`

**Interfaces:**

- Consumes: 无
- Produces: 可运行的 `pytest` 与可导入的 `agent_lens` 包

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_smoke.py`：

```python
def test_package_is_importable():
    import agent_lens

    assert agent_lens.__version__ == "0.1.0"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_smoke.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'agent_lens'`

- [ ] **Step 3: 写最小实现**

创建 `pyproject.toml`：

```toml
[project]
name = "agent-lens"
version = "0.1.0"
description = "Observability and cost analytics for coding agents"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.7"]

[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_lens"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

创建 `src/agent_lens/__init__.py`：

```python
"""agent-lens：coding agent 的观测与成本分析。"""

__version__ = "0.1.0"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_smoke.py -v`

Expected: PASS，1 passed

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/agent_lens/__init__.py tests/test_smoke.py uv.lock
git commit -m "chore: scaffold agent-lens python package"
```

---

### Task 2: TokenUsage 模型与包含关系校验

**Files:**

- Create: `src/agent_lens/models.py`
- Create: `tests/test_models.py`

**Interfaces:**

- Consumes: 无
- Produces:
  - `TokenUsage`（字段 `input_tokens`、`cached_input_tokens`、`cache_write_input_tokens`、`output_tokens`、`reasoning_output_tokens`、`total_tokens`，均为 `int`）
  - `TokenUsage.cache_hit_rate -> float`
  - `TokenUsage.uncached_input_tokens -> int`
  - `to_utc(iso_string: str) -> datetime`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_models.py`：

```python
import pytest
from pydantic import ValidationError

from agent_lens.models import TokenUsage, to_utc


def test_cache_hit_rate_is_cached_over_input():
    usage = TokenUsage(input_tokens=24207, cached_input_tokens=19200, output_tokens=653)

    assert usage.cache_hit_rate == pytest.approx(19200 / 24207)


def test_cache_hit_rate_is_zero_when_input_is_zero():
    usage = TokenUsage(input_tokens=0, cached_input_tokens=0)

    assert usage.cache_hit_rate == 0.0


def test_uncached_input_excludes_cached_part():
    usage = TokenUsage(input_tokens=24207, cached_input_tokens=19200)

    assert usage.uncached_input_tokens == 5007


def test_cached_input_may_not_exceed_input():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=100, cached_input_tokens=101)


def test_reasoning_output_may_not_exceed_output():
    with pytest.raises(ValidationError):
        TokenUsage(output_tokens=50, reasoning_output_tokens=51)


def test_reasoning_tokens_are_a_subset_of_output_tokens():
    usage = TokenUsage(output_tokens=263, reasoning_output_tokens=92)

    assert usage.output_tokens == 263


def test_to_utc_normalizes_z_suffix_to_utc():
    result = to_utc("2026-09-24T15:04:55.745Z")

    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 0
    assert result.hour == 15
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_models.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'agent_lens.models'`

- [ ] **Step 3: 写最小实现**

创建 `src/agent_lens/models.py`：

```python
"""Codex 会话日志的结构化模型。

全部结论依据对 8 个真实会话文件、321 条 token 记录的实测，
详见 docs/superpowers/specs/2026-09-24-agent-lens-design.md 第 4 节。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, model_validator


def to_utc(timestamp: str) -> datetime:
    """把 ISO 8601 字符串归一化为 UTC aware datetime。"""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)


class TokenUsage(BaseModel):
    """单次 API 调用的 token 用量。

    实测包含关系（321 条记录零例外）：
      cached_input_tokens <= input_tokens
      reasoning_output_tokens <= output_tokens

    因 output_tokens 已包含 reasoning_output_tokens，计费时两者不可相加。
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @model_validator(mode="after")
    def _validate_containment(self) -> TokenUsage:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError(
                f"cached_input_tokens({self.cached_input_tokens}) 不能大于 "
                f"input_tokens({self.input_tokens})"
            )
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError(
                f"reasoning_output_tokens({self.reasoning_output_tokens}) 不能大于 "
                f"output_tokens({self.output_tokens})"
            )
        return self

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率。input 为 0 时返回 0.0，避免除零。"""
        if self.input_tokens == 0:
            return 0.0
        return self.cached_input_tokens / self.input_tokens

    @property
    def uncached_input_tokens(self) -> int:
        """未命中缓存的 input，即按高价计费的部分。"""
        return self.input_tokens - self.cached_input_tokens
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_models.py -v`

Expected: PASS，7 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/models.py tests/test_models.py
git commit -m "feat: add TokenUsage model with containment validation"
```

---

### Task 3: 工具输出文本的元数据提取

**Files:**

- Create: `src/agent_lens/parser.py`
- Create: `tests/test_parser_metadata.py`

**Interfaces:**

- Consumes: 无
- Produces:
  - `extract_exec_metadata(output_text: str) -> tuple[int | None, float | None]`，返回 `(exit_code, wall_time_seconds)`
  - `derive_success(exit_code: int | None) -> bool | None`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_parser_metadata.py`：

```python
import pytest

from agent_lens.parser import derive_success, extract_exec_metadata


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Chunk ID: 7649ee\nWall time: 0.2849 seconds\nOutput:\nhello", (None, 0.2849)),
        ("Exit code: 1\nWall time: 5 seconds\nOutput:\n", (1, 5.0)),
        ("Exit code: 0\nWall time: 12.5 seconds\n", (0, 12.5)),
        ("纯粹的文本，没有任何元数据", (None, None)),
        ("Wall time: 3 seconds", (None, 3.0)),
    ],
)
def test_extract_exec_metadata(text, expected):
    assert extract_exec_metadata(text) == expected


def test_exit_code_must_be_at_line_start():
    """输出正文里出现的 Exit code 字样不应被误认。"""
    text = "输出内容里提到 Exit code: 1 这个词\nWall time: 1 seconds"

    assert extract_exec_metadata(text) == (None, 1.0)


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(0, True), (1, False), (127, False), (None, None)],
)
def test_derive_success(exit_code, expected):
    assert derive_success(exit_code) is expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_metadata.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'agent_lens.parser'`

- [ ] **Step 3: 写最小实现**

创建 `src/agent_lens/parser.py`：

```python
"""把 Codex 的 JSONL 会话文件解析成结构化对象。

本模块只做解析：不写数据库、不发网络请求、不修改源文件。
"""

from __future__ import annotations

import re

EXIT_CODE_RE = re.compile(r"^Exit code:\s*(-?\d+)\s*$", re.MULTILINE)
WALL_TIME_RE = re.compile(r"^Wall time:\s*([0-9.]+)\s*seconds", re.MULTILINE)


def extract_exec_metadata(output_text: str) -> tuple[int | None, float | None]:
    """从工具输出文本中提取退出码与墙钟耗时。

    实测 422 条 function_call_output 中 98% 带 Wall time、25% 带 Exit code。
    这是文本解析，属于兜底手段；结构化字段（如 patch_apply_end.success）优先。
    """
    exit_match = EXIT_CODE_RE.search(output_text)
    wall_match = WALL_TIME_RE.search(output_text)
    exit_code = int(exit_match.group(1)) if exit_match else None
    wall_time = float(wall_match.group(1)) if wall_match else None
    return exit_code, wall_time


def derive_success(exit_code: int | None) -> bool | None:
    """由退出码推导成功状态。无法推导时返回 None，不猜。"""
    if exit_code is None:
        return None
    return exit_code == 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_parser_metadata.py -v`

Expected: PASS，7 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/parser.py tests/test_parser_metadata.py
git commit -m "feat: extract exit code and wall time from tool output"
```

---

### Task 4: 记录模型与单行分派

**Files:**

- Modify: `src/agent_lens/models.py`
- Modify: `src/agent_lens/parser.py`
- Create: `tests/test_parser_line.py`

**Interfaces:**

- Consumes: `TokenUsage`、`to_utc`（Task 2）；`extract_exec_metadata`、`derive_success`（Task 3）
- Produces:
  - `ApiCallRecord`（`file_path`、`ordinal`、`session_id`、`turn_id`、`response_id`、`timestamp`、`usage`、`turn_input_tokens_cumulative`、`thread_input_tokens_cumulative`、`idempotency_key`）
  - `ToolCallRecord`（`file_path`、`ordinal`、`call_id`、`name`、`kind`、`arguments_raw`）
  - `ToolResultRecord`（`file_path`、`ordinal`、`call_id`、`output_text`、`exit_code`、`wall_time_seconds`、`success`）
  - `ItemCompletedRecord`（`file_path`、`ordinal`、`item_type`、`started_at_ms`、`completed_at_ms`、`duration_ms`）
  - `SessionMetaRecord`、`TurnContextRecord`、`TurnRecord`、`ParseError`、`ToolKind`
  - `parse_line(raw: str, file_path: str, ordinal: int) -> LineParseResult`
  - `LineParseResult`（可选字段：`session_meta`、`turn_context`、`api_call`、`tool_call`、`tool_result`、`item_completed`、`turn_completed`、`turn_aborted`、`event`、`parse_error`；方法 `is_empty()`）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_parser_line.py`：

```python
import json

from agent_lens.parser import parse_line


def test_parse_session_meta():
    raw = json.dumps(
        {
            "ordinal": 0,
            "type": "session_meta",
            "payload": {
                "session_id": "01a0d3ee",
                "timestamp": "2026-09-24T15:00:48.468Z",
                "cwd": "D:\\codex\\wzy_workstudio",
                "cli_version": "0.154.0",
                "model_provider": "deepseek",
                "base_instructions": {"text": "x" * 100},
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 0)

    assert result.session_meta.session_id == "01a0d3ee"
    assert result.session_meta.cli_version == "0.154.0"
    assert result.session_meta.base_instructions_chars == 100
    assert result.session_meta.cwd == "D:\\codex\\wzy_workstudio"


def test_parse_token_usage_record():
    raw = json.dumps(
        {
            "ordinal": 16,
            "type": "token_usage_record",
            "payload": {
                "thread_id": "t1",
                "turn_id": "turn1",
                "response_id": "resp1",
                "usage": {
                    "input_tokens": 18974,
                    "cached_input_tokens": 13184,
                    "output_tokens": 263,
                    "reasoning_output_tokens": 92,
                    "total_tokens": 19237,
                },
                "thread_token_usage": {"input_tokens": 43181},
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 16)

    assert result.api_call.usage.input_tokens == 18974
    assert result.api_call.thread_input_tokens_cumulative == 43181
    assert result.api_call.idempotency_key == ("f.jsonl", 16)


def test_parse_function_call_collects_arguments_as_raw_text():
    raw = json.dumps(
        {
            "ordinal": 5,
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": '{"command":["ls"]}',
                "call_id": "call_1",
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 5)

    assert result.tool_call.name == "exec_command"
    assert result.tool_call.kind == "function_call"
    assert result.tool_call.arguments_raw == '{"command":["ls"]}'


def test_parse_function_call_output_extracts_metadata():
    raw = json.dumps(
        {
            "ordinal": 6,
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "Exit code: 1\nWall time: 2 seconds\n",
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 6)

    assert result.tool_result.exit_code == 1
    assert result.tool_result.success is False
    assert result.tool_result.wall_time_seconds == 2.0


def test_parse_item_completed_computes_duration():
    raw = json.dumps(
        {
            "ordinal": 7,
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "CommandExecution", "id": "i1"},
                "started_at_ms": 1000,
                "completed_at_ms": 4500,
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 7)

    assert result.item_completed.item_type == "CommandExecution"
    assert result.item_completed.duration_ms == 3500


def test_parse_turn_context_records_model_and_effort():
    raw = json.dumps(
        {
            "ordinal": 2,
            "type": "turn_context",
            "payload": {"turn_id": "turn1", "model": "deepseek-v4-flash", "effort": "high"},
        }
    )

    result = parse_line(raw, "f.jsonl", 2)

    assert result.turn_context.model == "deepseek-v4-flash"
    assert result.turn_context.effort == "high"


def test_parse_task_complete_and_turn_aborted():
    complete = json.dumps(
        {
            "ordinal": 9,
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "t",
                "duration_ms": 300000,
            },
        }
    )
    aborted = json.dumps(
        {
            "ordinal": 10,
            "type": "event_msg",
            "payload": {"type": "turn_aborted", "turn_id": "t", "reason": "user_interrupt"},
        }
    )

    assert parse_line(complete, "f.jsonl", 9).turn_completed.duration_ms == 300000
    assert parse_line(aborted, "f.jsonl", 10).turn_aborted.aborted_reason == "user_interrupt"


def test_unknown_line_type_yields_empty_result():
    raw = json.dumps({"ordinal": 3, "type": "world_state", "payload": {}})

    result = parse_line(raw, "f.jsonl", 3)

    assert result.is_empty()
    assert result.parse_error is None


def test_broken_json_yields_parse_error():
    result = parse_line('{"ordinal": 4, "type": ', "f.jsonl", 4)

    assert result.parse_error is not None
    assert result.parse_error.reason == "invalid_json"


def test_token_record_without_cumulative_fields_still_parses():
    """0.153.4 之类的版本可能缺失累计字段与 session_id。"""
    raw = json.dumps(
        {
            "ordinal": 8,
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t",
                "response_id": "r",
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        }
    )

    result = parse_line(raw, "f.jsonl", 8)

    assert result.api_call.thread_input_tokens_cumulative is None
    assert result.api_call.session_id is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_line.py -v`

Expected: FAIL，`ImportError: cannot import name 'parse_line'`

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/models.py` 末尾追加（并把开头的 `from pydantic import BaseModel, model_validator` 改为 `from pydantic import BaseModel, Field, model_validator`，追加 `from typing import Literal`）：

```python
ToolKind = Literal["function_call", "custom_tool_call", "web_search_call", "tool_search_call"]


class SessionMetaRecord(BaseModel):
    """session_meta 行。"""

    session_id: str
    cwd: str | None = None
    cli_version: str | None = None
    model_provider: str | None = None
    recorded_at: datetime | None = None
    base_instructions_chars: int = 0


class TurnContextRecord(BaseModel):
    """turn_context 行，逐轮记录模型与推理强度。"""

    turn_id: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: str | None = None
    collaboration_mode: str | None = None


class ApiCallRecord(BaseModel):
    """token_usage_record：一次 LLM API 调用。

    thread_input_tokens_cumulative 只用于自检。实测该字段在每个文件内会重置，
    因此任何总量统计都必须对 usage 求和，不可读取累计字段（设计文档 4.2 节）。
    """

    file_path: str
    ordinal: int
    session_id: str | None = None
    turn_id: str | None = None
    response_id: str | None = None
    timestamp: datetime | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    turn_input_tokens_cumulative: int | None = None
    thread_input_tokens_cumulative: int | None = None

    @property
    def idempotency_key(self) -> tuple[str, int]:
        return (self.file_path, self.ordinal)


class ToolCallRecord(BaseModel):
    """工具调用请求。"""

    file_path: str
    ordinal: int
    call_id: str | None = None
    name: str
    kind: ToolKind = "function_call"
    arguments_raw: str = ""


class ToolResultRecord(BaseModel):
    """工具调用结果。"""

    file_path: str
    ordinal: int
    call_id: str | None = None
    output_text: str = ""
    exit_code: int | None = None
    wall_time_seconds: float | None = None
    success: bool | None = None


class ItemCompletedRecord(BaseModel):
    """item_completed 事件，带毫秒级起止时间。"""

    file_path: str
    ordinal: int
    item_type: str
    started_at_ms: int
    completed_at_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.completed_at_ms - self.started_at_ms)


class TurnRecord(BaseModel):
    """轮次的起止与结束原因。"""

    turn_id: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    aborted_reason: str | None = None


class ParseError(BaseModel):
    """单行解析失败的记录。"""

    file_path: str
    ordinal: int
    reason: str
    raw_preview: str
```

在 `src/agent_lens/parser.py` 顶部把 import 区替换为下面内容，并在 `derive_success` 之后追加大段实现：

```python
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from .models import (
    ApiCallRecord,
    ItemCompletedRecord,
    ParseError,
    SessionMetaRecord,
    TokenUsage,
    ToolCallRecord,
    ToolResultRecord,
    TurnContextRecord,
    TurnRecord,
    to_utc,
)

EXIT_CODE_RE = re.compile(r"^Exit code:\s*(-?\d+)\s*$", re.MULTILINE)
WALL_TIME_RE = re.compile(r"^Wall time:\s*([0-9.]+)\s*seconds", re.MULTILINE)

TOOL_CALL_TYPES = {
    "function_call": "function_call",
    "custom_tool_call": "custom_tool_call",
    "web_search_call": "web_search_call",
    "tool_search_call": "tool_search_call",
}

TOOL_RESULT_TYPES = ("function_call_output", "custom_tool_call_output")

RAW_PREVIEW_CHARS = 200
```

追加的实现：

```python
class LineParseResult(BaseModel):
    """单行的解析结果。每次最多命中一个有意义的字段。"""

    session_meta: SessionMetaRecord | None = None
    turn_context: TurnContextRecord | None = None
    api_call: ApiCallRecord | None = None
    tool_call: ToolCallRecord | None = None
    tool_result: ToolResultRecord | None = None
    item_completed: ItemCompletedRecord | None = None
    turn_completed: TurnRecord | None = None
    turn_aborted: TurnRecord | None = None
    event: dict | None = Field(default=None)
    parse_error: ParseError | None = None

    def is_empty(self) -> bool:
        """除 event 与 parse_error 外没有任何结构化结果。"""
        return all(
            getattr(self, name) is None
            for name in (
                "session_meta",
                "turn_context",
                "api_call",
                "tool_call",
                "tool_result",
                "item_completed",
                "turn_completed",
                "turn_aborted",
            )
        )


def _parse_error(file_path: str, ordinal: int, reason: str, raw: str) -> LineParseResult:
    return LineParseResult(
        parse_error=ParseError(
            file_path=file_path,
            ordinal=ordinal,
            reason=reason,
            raw_preview=raw[:RAW_PREVIEW_CHARS],
        )
    )


def _as_text(value: object) -> str:
    """把可能是字符串或对象的值统一成字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def parse_line(raw: str, file_path: str, ordinal: int) -> LineParseResult:
    """解析一行 JSONL。任何异常都转成 ParseError，不向上抛出。"""
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_error(file_path, ordinal, "invalid_json", raw)

    if not isinstance(row, dict):
        return _parse_error(file_path, ordinal, "not_an_object", raw)

    payload = row.get("payload")
    if not isinstance(payload, dict):
        return _parse_error(file_path, ordinal, "missing_payload", raw)

    try:
        return _dispatch(row.get("type"), payload, file_path, ordinal)
    except Exception:
        return _parse_error(file_path, ordinal, "invalid_shape", raw)


def _dispatch(row_type: object, payload: dict, file_path: str, ordinal: int) -> LineParseResult:
    if row_type == "session_meta":
        base = payload.get("base_instructions")
        text = base.get("text") if isinstance(base, dict) else None
        timestamp = payload.get("timestamp")
        return LineParseResult(
            session_meta=SessionMetaRecord(
                session_id=str(payload.get("session_id", "")),
                cwd=payload.get("cwd"),
                cli_version=payload.get("cli_version"),
                model_provider=payload.get("model_provider"),
                recorded_at=to_utc(timestamp) if timestamp else None,
                base_instructions_chars=len(text) if isinstance(text, str) else 0,
            )
        )

    if row_type == "turn_context":
        mode = payload.get("collaboration_mode")
        return LineParseResult(
            turn_context=TurnContextRecord(
                turn_id=payload.get("turn_id"),
                model=payload.get("model"),
                effort=payload.get("effort"),
                cwd=payload.get("cwd"),
                collaboration_mode=mode.get("mode") if isinstance(mode, dict) else None,
            )
        )

    if row_type == "token_usage_record":
        thread_usage = payload.get("thread_token_usage") or {}
        turn_usage = payload.get("turn_token_usage") or {}
        timestamp = payload.get("timestamp")
        return LineParseResult(
            api_call=ApiCallRecord(
                file_path=file_path,
                ordinal=ordinal,
                session_id=payload.get("session_id"),
                turn_id=payload.get("turn_id"),
                response_id=payload.get("response_id"),
                timestamp=to_utc(timestamp) if timestamp else None,
                usage=TokenUsage.model_validate(payload.get("usage") or {}),
                turn_input_tokens_cumulative=turn_usage.get("input_tokens"),
                thread_input_tokens_cumulative=thread_usage.get("input_tokens"),
            )
        )

    if row_type == "response_item":
        return _dispatch_response_item(payload, file_path, ordinal)

    if row_type == "event_msg":
        return _dispatch_event(payload, file_path, ordinal)

    return LineParseResult()


def _dispatch_response_item(payload: dict, file_path: str, ordinal: int) -> LineParseResult:
    item_type = payload.get("type")

    if item_type in TOOL_CALL_TYPES:
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = payload.get("input")
        return LineParseResult(
            tool_call=ToolCallRecord(
                file_path=file_path,
                ordinal=ordinal,
                call_id=payload.get("call_id"),
                name=str(payload.get("name", "")),
                kind=TOOL_CALL_TYPES[item_type],
                arguments_raw=_as_text(arguments),
            )
        )

    if item_type in TOOL_RESULT_TYPES:
        output_text = _as_text(payload.get("output"))
        exit_code, wall_time = extract_exec_metadata(output_text)
        return LineParseResult(
            tool_result=ToolResultRecord(
                file_path=file_path,
                ordinal=ordinal,
                call_id=payload.get("call_id"),
                output_text=output_text,
                exit_code=exit_code,
                wall_time_seconds=wall_time,
                success=derive_success(exit_code),
            )
        )

    return LineParseResult()


def _dispatch_event(payload: dict, file_path: str, ordinal: int) -> LineParseResult:
    event_type = payload.get("type")

    if event_type == "item_completed":
        item = payload.get("item")
        item = item if isinstance(item, dict) else {}
        return LineParseResult(
            item_completed=ItemCompletedRecord(
                file_path=file_path,
                ordinal=ordinal,
                item_type=str(item.get("type", "")),
                started_at_ms=int(payload.get("started_at_ms", 0)),
                completed_at_ms=int(payload.get("completed_at_ms", 0)),
            )
        )

    if event_type == "task_complete":
        return LineParseResult(
            turn_completed=TurnRecord(
                turn_id=str(payload.get("turn_id", "")),
                duration_ms=payload.get("duration_ms"),
            )
        )

    if event_type == "turn_aborted":
        return LineParseResult(
            turn_aborted=TurnRecord(
                turn_id=str(payload.get("turn_id", "")),
                duration_ms=payload.get("duration_ms"),
                aborted_reason=payload.get("reason"),
            )
        )

    return LineParseResult(event={"event_type": event_type, "payload": payload})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_parser_line.py -v`

Expected: PASS，11 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/models.py src/agent_lens/parser.py tests/test_parser_line.py
git commit -m "feat: dispatch each codex jsonl line into typed records"
```

---

### Task 5: 会话文件解析与边界容错

**Files:**

- Modify: `src/agent_lens/models.py`
- Modify: `src/agent_lens/parser.py`
- Create: `tests/conftest.py`
- Create: `tests/test_parser_session.py`

**Interfaces:**

- Consumes: `parse_line`、`LineParseResult`（Task 4）
- Produces:
  - `ParsedSession`（字段 `session_id`、`file_path`、`cli_version`、`cwd`、`model_provider`、`base_instructions_chars`、`recorded_at`、`turns`、`turn_contexts`、`api_calls`、`tool_calls`、`tool_results`、`items`、`events`、`parse_errors`、`partial_tail`；属性 `total_input_tokens`、`last_thread_input_tokens`）
  - `iter_jsonl(path: Path, start_offset: int = 0) -> Iterator[tuple[int, str]]`
  - `parse_session_file(path: Path) -> ParsedSession`

- [ ] **Step 1: 写失败的测试**

创建 `tests/conftest.py`：

```python
import json
from pathlib import Path

import pytest


@pytest.fixture
def write_jsonl(tmp_path: Path):
    """把若干行对象写成一个 JSONL 文件，返回路径。"""

    def _write(rows: list[dict | str], name: str = "rollout-2026-09-24T23-00-48-01a0d3ee-f6d2-7af3-80e0-641744b01963.jsonl") -> Path:
        target = tmp_path / name
        with target.open("w", encoding="utf-8") as handle:
            for row in rows:
                if isinstance(row, str):
                    handle.write(row + "\n")
                else:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return target

    return _write
```

创建 `tests/test_parser_session.py`：

```python
from agent_lens.parser import parse_session_file


def meta_line(session_id: str = "01a0d3ee-f6d2-7af3-80e0-641744b01963", chars: int = 17766) -> dict:
    return {
        "ordinal": 0,
        "type": "session_meta",
        "payload": {
            "session_id": session_id,
            "timestamp": "2026-09-24T15:00:48.468Z",
            "cwd": "D:\\codex\\wzy_workstudio",
            "cli_version": "0.154.0",
            "model_provider": "deepseek",
            "base_instructions": {"text": "x" * chars},
        },
    }


def usage_line(ordinal: int, input_tokens: int, thread_cumulative: int) -> dict:
    return {
        "ordinal": ordinal,
        "type": "token_usage_record",
        "payload": {
            "turn_id": "turn1",
            "response_id": f"resp{ordinal}",
            "usage": {"input_tokens": input_tokens, "output_tokens": 10},
            "thread_token_usage": {"input_tokens": thread_cumulative},
        },
    }


def test_parses_meta_and_usage_rows(write_jsonl):
    path = write_jsonl([meta_line(), usage_line(16, 18974, 18974), usage_line(28, 24207, 43181)])

    parsed = parse_session_file(path)

    assert parsed.session_id == "01a0d3ee-f6d2-7af3-80e0-641744b01963"
    assert parsed.cli_version == "0.154.0"
    assert parsed.base_instructions_chars == 17766
    assert len(parsed.api_calls) == 2
    assert parsed.total_input_tokens == 43181
    assert parsed.last_thread_input_tokens == 43181


def test_incomplete_last_line_is_kept_as_partial_tail(write_jsonl):
    truncated = '{"ordinal": 99, "type": "token_usage_record", "payload": {"usage": {"input'
    path = write_jsonl([meta_line(), usage_line(16, 100, 100), truncated])

    parsed = parse_session_file(path)

    assert parsed.partial_tail == truncated
    assert parsed.parse_errors == []
    assert len(parsed.api_calls) == 1


def test_broken_middle_line_is_recorded_but_does_not_stop_parsing(write_jsonl):
    path = write_jsonl(
        [meta_line(), usage_line(16, 100, 100), '{"ordinal": 17, 坏的', usage_line(18, 200, 300)]
    )

    parsed = parse_session_file(path)

    assert len(parsed.parse_errors) == 1
    assert parsed.parse_errors[0].reason == "invalid_json"
    assert len(parsed.api_calls) == 2
    assert parsed.total_input_tokens == 300


def test_very_long_line_is_parsed(write_jsonl):
    path = write_jsonl([meta_line(chars=273823)])

    parsed = parse_session_file(path)

    assert parsed.base_instructions_chars == 273823


def test_session_without_usage_records_is_not_an_error(write_jsonl):
    """0.153.4 的会话完全没有 token_usage_record。"""
    path = write_jsonl(
        [meta_line(), {"ordinal": 1, "type": "event_msg", "payload": {"type": "task_started"}}]
    )

    parsed = parse_session_file(path)

    assert parsed.api_calls == []
    assert parsed.total_input_tokens == 0
    assert parsed.last_thread_input_tokens is None
    assert parsed.parse_errors == []


def test_empty_file_does_not_raise(write_jsonl):
    path = write_jsonl([])

    parsed = parse_session_file(path)

    assert parsed.api_calls == []
    assert parsed.partial_tail is None


def test_session_id_falls_back_to_filename(write_jsonl):
    path = write_jsonl(
        [], name="rollout-2026-09-24T23-00-48-01a0beef-f6d2-7af3-80e0-641744b01963.jsonl"
    )

    parsed = parse_session_file(path)

    assert parsed.session_id == "01a0beef-f6d2-7af3-80e0-641744b01963"


def test_row_ordinal_wins_over_line_number(write_jsonl):
    path = write_jsonl([meta_line(), usage_line(42, 100, 100)])

    parsed = parse_session_file(path)

    assert parsed.api_calls[0].ordinal == 42


def test_turn_complete_and_aborted_merge_into_one_turn(write_jsonl):
    completed = {
        "ordinal": 5,
        "type": "event_msg",
        "payload": {"type": "task_complete", "turn_id": "t1", "duration_ms": 300000},
    }
    aborted = {
        "ordinal": 6,
        "type": "event_msg",
        "payload": {"type": "turn_aborted", "turn_id": "t1", "reason": "user_interrupt"},
    }
    path = write_jsonl([meta_line(), completed, aborted])

    parsed = parse_session_file(path)

    assert len(parsed.turns) == 1
    assert parsed.turns[0].duration_ms == 300000
    assert parsed.turns[0].aborted_reason == "user_interrupt"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_session.py -v`

Expected: FAIL，`ImportError: cannot import name 'parse_session_file'`

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/models.py` 末尾追加：

```python
class ParsedSession(BaseModel):
    """一个会话文件的完整解析结果。

    一个 Codex 会话可能横跨多个文件（设计文档 4.3 节），因此本对象对应的是
    「一个文件」，跨文件的会话合并由存储层负责。
    """

    session_id: str
    file_path: str
    cli_version: str | None = None
    cwd: str | None = None
    model_provider: str | None = None
    base_instructions_chars: int = 0
    recorded_at: datetime | None = None
    turns: list[TurnRecord] = Field(default_factory=list)
    turn_contexts: list[TurnContextRecord] = Field(default_factory=list)
    api_calls: list[ApiCallRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tool_results: list[ToolResultRecord] = Field(default_factory=list)
    items: list[ItemCompletedRecord] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    parse_errors: list[ParseError] = Field(default_factory=list)
    partial_tail: str | None = None

    @property
    def total_input_tokens(self) -> int:
        """总量一律对单次 usage 求和，绝不读取累计字段（设计文档 4.2 节）。"""
        return sum(call.usage.input_tokens for call in self.api_calls)

    @property
    def last_thread_input_tokens(self) -> int | None:
        """文件内最后一条非空累计值，只用于自检。"""
        for call in reversed(self.api_calls):
            if call.thread_input_tokens_cumulative is not None:
                return call.thread_input_tokens_cumulative
        return None
```

在 `src/agent_lens/parser.py` 的 import 区加上 `from collections.abc import Iterator`、`from pathlib import Path`，并确保 `.models` 的引入里包含 `ParsedSession`。追加实现：

```python
UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def iter_jsonl(path: Path, start_offset: int = 0) -> Iterator[tuple[int, str]]:
    """按行迭代 JSONL 文件，产出 (行号, 行文本)，行号从 1 开始。

    errors="replace" 用于兜底：日志里可能混入非法编码字节，不应因此中断整个文件。
    本函数是文本模式；带字节偏移的增量读取由 P1.3 的采集器负责。
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if start_offset:
            handle.seek(start_offset)
        for line_number, raw in enumerate(handle, start=1):
            yield line_number, raw.rstrip("\n")


def _session_id_from_filename(path: Path) -> str:
    """从文件名里取最后一个 UUID 作为会话 ID 的兜底。"""
    matches = UUID_RE.findall(path.stem)
    return matches[-1] if matches else path.stem


def _upsert_turn(parsed: ParsedSession, turn: TurnRecord) -> None:
    """按 turn_id 合并 task_complete 与 turn_aborted。"""
    for existing in parsed.turns:
        if existing.turn_id == turn.turn_id:
            existing.started_at = turn.started_at or existing.started_at
            existing.completed_at = turn.completed_at or existing.completed_at
            existing.duration_ms = turn.duration_ms or existing.duration_ms
            existing.aborted_reason = turn.aborted_reason or existing.aborted_reason
            return
    parsed.turns.append(turn)


def _merge(parsed: ParsedSession, result: LineParseResult) -> None:
    if result.session_meta is not None:
        meta = result.session_meta
        parsed.session_id = meta.session_id or parsed.session_id
        parsed.cli_version = meta.cli_version
        parsed.cwd = meta.cwd
        parsed.model_provider = meta.model_provider
        parsed.recorded_at = meta.recorded_at
        parsed.base_instructions_chars = meta.base_instructions_chars
    if result.turn_context is not None:
        parsed.turn_contexts.append(result.turn_context)
    if result.api_call is not None:
        parsed.api_calls.append(result.api_call)
    if result.tool_call is not None:
        parsed.tool_calls.append(result.tool_call)
    if result.tool_result is not None:
        parsed.tool_results.append(result.tool_result)
    if result.item_completed is not None:
        parsed.items.append(result.item_completed)
    for turn in (result.turn_completed, result.turn_aborted):
        if turn is not None:
            _upsert_turn(parsed, turn)
    if result.event is not None:
        parsed.events.append(result.event)
    if result.parse_error is not None:
        parsed.parse_errors.append(result.parse_error)


def parse_session_file(path: Path) -> ParsedSession:
    """解析一个会话文件。

    容错策略：
      单行损坏        -> 记入 parse_errors，继续解析
      末尾半行        -> 存入 partial_tail，不计错误（补齐后重新解析）
      缺 session_meta -> 会话 ID 回退为文件名里的 UUID
    """
    file_path = str(path)
    parsed = ParsedSession(session_id=_session_id_from_filename(path), file_path=file_path)
    lines = list(iter_jsonl(path))
    if not lines:
        return parsed

    last_line_number = lines[-1][0]
    for line_number, raw in lines:
        if not raw.strip():
            continue
        if line_number == last_line_number and not raw.rstrip().endswith("}"):
            parsed.partial_tail = raw
            continue
        _merge(parsed, parse_line(raw, file_path, line_number))
    return parsed
```

同时修改 `parse_line`，让记录使用行内自带的 `ordinal`，行号只作兜底。在 `payload` 校验通过之后、调用 `_dispatch` 之前插入一行：

```python
    ordinal = int(row.get("ordinal", ordinal))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_parser_session.py -v`

Expected: PASS，9 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/models.py src/agent_lens/parser.py tests/conftest.py tests/test_parser_session.py
git commit -m "feat: parse whole session files with partial-tail tolerance"
```

---

### Task 6: 累计字段自检

**Files:**

- Modify: `src/agent_lens/models.py`
- Modify: `src/agent_lens/parser.py`
- Create: `tests/test_verification.py`

**Interfaces:**

- Consumes: `ParsedSession`（Task 5）
- Produces:
  - `VerificationResult`（字段 `file_path`、`session_id`、`summed_input_tokens`、`last_thread_input_tokens`、`matches`）
  - `verify_thread_totals(parsed: ParsedSession) -> VerificationResult`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_verification.py`：

```python
from agent_lens.parser import parse_session_file, verify_thread_totals
from tests.test_parser_session import meta_line, usage_line


def test_matches_when_last_cumulative_equals_sum(write_jsonl):
    path = write_jsonl([meta_line(), usage_line(16, 18974, 18974), usage_line(28, 24207, 43181)])

    result = verify_thread_totals(parse_session_file(path))

    assert result.matches is True
    assert result.summed_input_tokens == 43181
    assert result.last_thread_input_tokens == 43181


def test_reports_mismatch_when_cumulative_resets(write_jsonl):
    """模拟真实情况：累计字段在文件内被重置。"""
    path = write_jsonl([meta_line(), usage_line(16, 100, 100), usage_line(28, 200, 200)])

    result = verify_thread_totals(parse_session_file(path))

    assert result.matches is False
    assert result.summed_input_tokens == 300
    assert result.last_thread_input_tokens == 200


def test_session_without_usage_records_has_no_mismatch(write_jsonl):
    path = write_jsonl([meta_line()])

    result = verify_thread_totals(parse_session_file(path))

    assert result.matches is True
    assert result.summed_input_tokens == 0
    assert result.last_thread_input_tokens is None
```

注意：`from tests.test_parser_session import ...` 要求 `tests` 目录可被导入。在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 里把 `pythonpath` 改为 `["src", "."]`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_verification.py -v`

Expected: FAIL，`ImportError: cannot import name 'verify_thread_totals'`

- [ ] **Step 3: 写最小实现**

在 `src/agent_lens/models.py` 末尾追加：

```python
class VerificationResult(BaseModel):
    """累计字段自检结果，用于回归测试与数据质量告警。"""

    file_path: str
    session_id: str
    summed_input_tokens: int
    last_thread_input_tokens: int | None
    matches: bool
```

在 `src/agent_lens/parser.py` 末尾追加（并把 `VerificationResult` 加入 `.models` 的引入）：

```python
def verify_thread_totals(parsed: ParsedSession) -> VerificationResult:
    """校验 Σ usage.input_tokens 是否等于文件内最后一条累计值。

    这是设计文档 4.2 节的回归测试。等价关系只在单个文件内成立：
    thread_token_usage 会跨文件重置，跨文件的会话必须按文件分别求和再相加。
    """
    summed = parsed.total_input_tokens
    last = parsed.last_thread_input_tokens
    return VerificationResult(
        file_path=parsed.file_path,
        session_id=parsed.session_id,
        summed_input_tokens=summed,
        last_thread_input_tokens=last,
        matches=(summed == 0) if last is None else (last == summed),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_verification.py -v`

Expected: PASS，3 passed

- [ ] **Step 5: 提交**

```bash
git add src/agent_lens/models.py src/agent_lens/parser.py tests/test_verification.py pyproject.toml
git commit -m "feat: verify summed usage against last cumulative value"
```

---

### Task 7: 真实数据 fixture 与端到端验证

**Files:**

- Create: `scripts/make_fixture.py`
- Create: `tests/fixtures/real_session_sample.jsonl`（由脚本生成）
- Create: `tests/fixtures/real_session_sample.meta.json`（由脚本生成）
- Create: `tests/test_parser_real_fixture.py`

**Interfaces:**

- Consumes: `parse_session_file`、`verify_thread_totals`（Task 5、Task 6）
- Produces: 一份可提交到公开仓库的脱敏真实样本，以及基于它的回归测试

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_parser_real_fixture.py`：

```python
import json
from pathlib import Path

from agent_lens.parser import parse_session_file, verify_thread_totals

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_real_fixture_totals_match_recorded_meta():
    fixture = FIXTURE_DIR / "real_session_sample.jsonl"
    meta = json.loads((FIXTURE_DIR / "real_session_sample.meta.json").read_text(encoding="utf-8"))

    parsed = parse_session_file(fixture)

    assert len(parsed.api_calls) == meta["api_call_count"]
    assert parsed.total_input_tokens == meta["summed_input_tokens"]


def test_real_fixture_passes_self_check():
    parsed = parse_session_file(FIXTURE_DIR / "real_session_sample.jsonl")

    result = verify_thread_totals(parsed)

    assert result.matches is True


def test_real_fixture_contains_no_obvious_secrets():
    text = (FIXTURE_DIR / "real_session_sample.jsonl").read_text(encoding="utf-8")

    for marker in ("ghp_", "github_pat_", "sk-", "AKIA", "BEGIN RSA PRIVATE KEY"):
        assert marker not in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_parser_real_fixture.py -v`

Expected: FAIL，`FileNotFoundError`，fixture 尚不存在

- [ ] **Step 3: 写 fixture 生成脚本**

创建 `scripts/make_fixture.py`：

```python
"""从真实 Codex 会话日志生成脱敏的测试 fixture。

用法：
    uv run python scripts/make_fixture.py <真实会话文件> tests/fixtures/real_session_sample.jsonl

处理规则只改正文，不动任何数值字段，因此 token 总量保持不变：
  - base_instructions.text 替换为等长的 x 填充（长度保留，内容不保留）
  - message / reasoning 的正文替换为 [SAMPLE]
  - function_call 的 arguments 截断到 120 字符
  - 工具输出的 output 只保留元数据行（Chunk ID / Wall time / Exit code / Output）
  - 密钥形态替换为 [REDACTED]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

METADATA_LINE_RE = re.compile(r"^(Chunk ID:|Wall time:|Exit code:|Output:).*$", re.MULTILINE)

ARGUMENTS_LIMIT = 120
SAMPLE = "[SAMPLE]"
MESSAGE_ITEM_TYPES = {"message", "reasoning"}


def redact_text(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def keep_metadata_only(text: str) -> str:
    return "\n".join(METADATA_LINE_RE.findall(text)) + "\n"


def sanitize_payload(payload: dict) -> None:
    base = payload.get("base_instructions")
    if isinstance(base, dict) and isinstance(base.get("text"), str):
        base["text"] = "x" * len(base["text"])
    if payload.get("type") in MESSAGE_ITEM_TYPES:
        payload["content"] = [SAMPLE]
        if payload.get("summary"):
            payload["summary"] = [SAMPLE]
    for key in ("arguments", "input"):
        if isinstance(payload.get(key), str):
            payload[key] = payload[key][:ARGUMENTS_LIMIT]
    if isinstance(payload.get("output"), str):
        payload["output"] = keep_metadata_only(payload["output"])
    if isinstance(payload.get("message"), str):
        payload["message"] = SAMPLE


def sanitize_line(raw: str) -> str:
    row = json.loads(raw)
    payload = row.get("payload")
    if isinstance(payload, dict):
        sanitize_payload(payload)
    return redact_text(json.dumps(row, ensure_ascii=False))


def main(source: str, target: str) -> None:
    source_path = Path(source).expanduser()
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    kept_api_calls = 0
    summed_input_tokens = 0
    out_lines: list[str] = []

    for raw in source_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "token_usage_record":
            kept_api_calls += 1
            summed_input_tokens += int(row["payload"].get("usage", {}).get("input_tokens", 0))
        out_lines.append(sanitize_line(raw))

    target_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    meta_path = target_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "source_file": source_path.name,
                "api_call_count": kept_api_calls,
                "summed_input_tokens": summed_input_tokens,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {target_path} ({len(out_lines)} lines) and {meta_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 4: 生成 fixture 并跑测试**

Run（换成一个真实的会话文件路径）：

```bash
uv run python scripts/make_fixture.py \
  "$HOME/.codex/sessions/2026/09/22/rollout-2026-09-22T19-42-24-01a0c8ec-9a65-78e0-828c-4a0134da40a4.jsonl" \
  tests/fixtures/real_session_sample.jsonl
uv run pytest tests/test_parser_real_fixture.py -v
```

Windows 下把 `$HOME` 换成 `$env:USERPROFILE`。

Expected: PASS，3 passed

- [ ] **Step 5: 人工检查 fixture 里没有敏感内容**

打开 `tests/fixtures/real_session_sample.jsonl`，确认没有密钥、没有对话正文、没有业务代码内容，且 `token_usage_record` 的数值字段完好。

这一步不能跳过：fixture 会进入公开仓库。

- [ ] **Step 6: 提交**

```bash
git add scripts/make_fixture.py tests/fixtures/ tests/test_parser_real_fixture.py
git commit -m "test: add redacted real-session fixture and parser regression tests"
```

---

## Self-Review

### Spec coverage

| 设计文档要求 | 覆盖位置 |
|---|---|
| 4.2 累计字段跨文件重置、总量对 usage 求和 | Task 2、Task 5、Task 6 |
| 4.3 一个会话跨多个文件 | Task 5 的 `ParsedSession` 文档字符串与 Task 6 的自检口径 |
| 4.4 字段包含关系 | Task 2 |
| 4.5 读取时文件仍在增长 | Task 5 的 `partial_tail` |
| 4.6 Schema 漂移、缺 token 记录 | Task 4、Task 5 |
| 4.7 超长行 | Task 5 |
| 4.1 数据源结构与各类行 | Task 4 |
| 6.1 层级：session / turn / api_call / tool_call | Task 4、Task 5 |
| 6.3 核心表的字段来源 | Task 4、Task 5 |
| 6.5 工具耗时与失败率的数据来源 | Task 3、Task 4 |
| 6.9 模型与推理强度 | Task 4 的 `turn_context` |
| 8 脱敏（fixture 层面） | Task 7 |

project 归属、pricing、SQLite 入库、Langfuse 上报、前端均不在本计划范围，分别属于 P1.2 到 P1.4。

### Placeholder scan

全文无 TBD、无 TODO、无「类似 Task N」的省略。每个代码步骤都给了完整可运行代码。

### Type consistency

- `ordinal` 在 `ApiCallRecord`、`ToolCallRecord`、`ToolResultRecord`、`ItemCompletedRecord`、`ParseError` 上都是 `int`。
- `idempotency_key` 返回 `tuple[str, int]`，与设计文档的 `(文件路径, ordinal)` 一致。
- `LineParseResult` 的字段名与 `_merge` 里读取的名字逐一对应。
- `ParsedSession.total_input_tokens` 与 `verify_thread_totals` 使用的是同一个 `usage.input_tokens` 求和口径。
- `ApiCallRecord.timestamp` 与 `SessionMetaRecord.recorded_at` 都是 `datetime | None`，解析时统一走 `to_utc`。

## Execution Handoff

计划已完成并保存到 `docs/superpowers/plans/2026-09-25-p1-1-codex-log-parser.md`。两种执行方式：

**1. 子代理驱动（推荐）** —— 每个 Task 派一个全新的子代理去实现，我在 Task 之间审查，上下文干净、迭代快。

**2. 当前会话内执行** —— 用 `superpowers:executing-plans` 在当前会话里成批执行，带检查点。

你选哪种？
