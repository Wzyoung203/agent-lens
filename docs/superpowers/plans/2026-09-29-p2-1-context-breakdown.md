# P2.1 上下文成本分解 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每次 API 调用的 `input_tokens` 拆成「固定指令 / skill 目录 / 历史对话 / 工具输出 / 未归因」五块，落成可重放的投影表，并通过 API 与中文前端呈现。

**Architecture:** 新增 `context.py` 作为纯重放层（输入原始 JSONL，输出每调用一条分解记录，除读文件外无 IO 副作用）；新增独立命令 `agent-lens analyze` 驱动它写库；`queries.py` + `api/routes/context.py` 提供聚合查询；前端新增「上下文成本」页。采集器完全不动——正文不在库里，分解只能回事实源算，这件事不该塞进已经很复杂的采集循环。

**Tech Stack:** Python 3.12、pydantic v2、SQLite、FastAPI、pytest、Vue 3 + TypeScript + ECharts、uv。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-lens-design.md`（重点 3.2 阶段 2、6.7 上下文成本分解、9.1 上下文成本页、13.2 已结案的 reasoning 计价）

## Global Constraints

- Python `>=3.12`；不新增运行期依赖（分解只用标准库 + pydantic）。
- **事实源只读**：任何代码都不得写入、重命名或删除 `~/.codex/sessions` 下的文件。
- **正文不入库**：只存长度与派生数值，绝不存消息、推理或工具输出正文。
- 幂等键统一 `(file_path, ordinal)`；本计划新增表用 `(file_path, ordinal, block)` 作主键，重跑不得产生重复行。
- 时间一律 UTC aware；金额单位随 `currency`（默认 USD）。
- `output_tokens` 已含 `reasoning_output_tokens`，两者不可相加（设计文档 4.4）。
- 标识符用英文，注释与 docstring 用中文。

## 已完成的先期验证（执行前必读，它决定了两处设计）

1. **文件顺序（实测）**：一次调用在 JSONL 里的顺序是
   `response_item(reasoning) → response_item(function_call) → token_usage_record → response_item(function_call_output)`。
   `token_usage_record` 排在**本次调用产出之后、工具输出之前**。所以「把本次产出也算进本次 input」是错的；
   正确做法是：模型产出的条目先进 buffer，遇到工具输出才把 buffer 提交进上下文，`token_usage_record`
   取的是**不含 buffer 的已提交快照**。
2. **校准结论（实测 918 次调用）**：按官方比率（中文 0.6 token/字符、其他 0.3 token/字符）纯字符估算，
   `真实 input / 估算` 中位数 **1.206**（p10 1.008、p90 1.574）。纯字符估算系统性偏低约 17%，差额来自每次调用
   固定重发的工具定义与请求框架。**因此必须以真实 `input_tokens` 为锚**：四块按估算值摊派后，余量单列成第五块
   「未归因」，保证五块之和恒等于真实分母；若估算超过真实值，则四块等比缩小、未归因记 0。
3. **`base_instructions` 是对象不是字符串**：`session_meta.payload.base_instructions` 形如 `{"text": "..."}`，
   直接 `len()` 会得到 0（原型第一版就踩了这个坑），必须取 `.text`。

---

## Task 1: `context.py` 重放分解核心

**Files:**
- Create: `src/agent_lens/context.py`
- Test: `tests/test_context_decompose.py`

**Interfaces:**

- Consumes: 无（纯标准库）
- Produces（后续任务依赖这些名字，不要改名）：
  - `CJK_TOKENS_PER_CHAR = 0.6`、`OTHER_TOKENS_PER_CHAR = 0.3`
  - `BLOCK_FIXED_INSTRUCTIONS`、`BLOCK_SKILL_CATALOG`、`BLOCK_HISTORY`、`BLOCK_TOOL_OUTPUT`、`BLOCK_UNATTRIBUTED`（值分别为 `fixed_instructions` / `skill_catalog` / `history` / `tool_output` / `unattributed`）
  - `BLOCKS: tuple[str, ...]`：**只含前四个字符块**，`unattributed` 不在其中
  - `@dataclass(frozen=True) class BlockChars: cjk: int = 0; other: int = 0`，带只读属性 `estimated_tokens -> float`
  - `@dataclass(frozen=True) class CallBreakdown`：字段 `file_path, ordinal, session_id, turn_id, input_tokens, blocks: dict[str, BlockChars], unattributed_tokens: float`
  - `def split_chars(text: str) -> tuple[int, int]` → `(cjk, other)`
  - `def attribute(blocks, input_tokens) -> tuple[dict[str, float], float]`：不变量 `sum(各块) + 未归因 == input_tokens`
  - `def decompose_lines(lines: Iterable[str], *, file_path: str) -> list[CallBreakdown]`
  - `def decompose_file(path: Path) -> list[CallBreakdown]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_decompose.py
import json

from agent_lens import context


def _meta(base: str, session_id: str = "s1") -> dict:
    return {"type": "session_meta",
            "payload": {"session_id": session_id, "base_instructions": {"text": base}}}


def _ri(payload: dict) -> dict:
    return {"type": "response_item", "payload": payload}


def _tur(ordinal: int, input_tokens: int, turn_id: str = "t1") -> dict:
    return {"type": "token_usage_record", "ordinal": ordinal,
            "payload": {"session_id": "s1", "turn_id": turn_id,
                        "usage": {"input_tokens": input_tokens}}}


def _lines(*rows: dict) -> list[str]:
    return [json.dumps(r, ensure_ascii=False) for r in rows]


def test_split_chars_separates_cjk_from_ascii():
    assert context.split_chars("abc") == (0, 3)
    assert context.split_chars("中文") == (2, 0)
    assert context.split_chars("a中") == (1, 1)


def test_attribute_sums_to_real_input_tokens():
    blocks = {
        context.BLOCK_FIXED_INSTRUCTIONS: context.BlockChars(other=100),
        context.BLOCK_HISTORY: context.BlockChars(other=100),
    }
    attributed, unattributed = context.attribute(blocks, input_tokens=1000)
    assert attributed[context.BLOCK_FIXED_INSTRUCTIONS] == 30.0
    assert attributed[context.BLOCK_HISTORY] == 30.0
    assert unattributed == 940.0  # 字符估算覆盖不到的部分单列，不摊进四块
    assert sum(attributed.values()) + unattributed == 1000


def test_attribute_scales_down_when_estimate_exceeds_real():
    blocks = {context.BLOCK_HISTORY: context.BlockChars(other=1000)}
    attributed, unattributed = context.attribute(blocks, input_tokens=100)
    assert attributed[context.BLOCK_HISTORY] == 100.0
    assert unattributed == 0.0


def test_snapshot_excludes_output_produced_by_the_same_call():
    """本次调用的 reasoning/function_call 不进本次快照；工具输出之后才进上下文。"""
    lines = _lines(
        _meta("BASE"),
        _ri({"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "U" * 10}]}),
        _ri({"type": "reasoning", "content": [{"type": "reasoning_text", "text": "R" * 100}]}),
        _ri({"type": "function_call", "name": "exec_command", "arguments": "A" * 100}),
        _tur(4, 1000),
        _ri({"type": "function_call_output", "output": "O" * 100}),
        _ri({"type": "reasoning", "content": [{"type": "reasoning_text", "text": "R" * 100}]}),
        _tur(7, 2000),
    )
    first, second = context.decompose_lines(lines, file_path="f.jsonl")

    # 第一次调用：只有固定指令 + 用户消息进上下文，本次的 reasoning/function_call 不算
    assert first.blocks[context.BLOCK_HISTORY].other == 10
    assert first.blocks[context.BLOCK_TOOL_OUTPUT].other == 0
    # 第二次调用：上一轮的 reasoning(100)+function_call(100)+工具输出(100) 都已提交
    assert second.blocks[context.BLOCK_HISTORY].other == 210
    assert second.blocks[context.BLOCK_TOOL_OUTPUT].other == 100
    assert (first.ordinal, second.ordinal) == (4, 7)


def test_developer_message_goes_to_skill_catalog_block():
    lines = _lines(
        _meta("BASE"),
        _ri({"type": "message", "role": "developer",
             "content": [{"type": "input_text", "text": "D" * 20}]}),
        _tur(2, 100),
    )
    (only,) = context.decompose_lines(lines, file_path="f.jsonl")
    assert only.blocks[context.BLOCK_SKILL_CATALOG].other == 20
    assert only.blocks[context.BLOCK_HISTORY].other == 0


def test_decompose_file_reads_from_disk(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    path.write_text("\n".join(_lines(_meta("BASE"), _tur(1, 50))), encoding="utf-8")
    (only,) = context.decompose_file(path)
    assert only.input_tokens == 50
    assert only.session_id == "s1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_context_decompose.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'agent_lens.context'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agent_lens/context.py
"""把每次 API 调用的 input 重放成「上下文构成」（设计文档 6.7 节）。

正文不入库，所以分解只能回原始 JSONL 重放。两处关键口径见计划文档「已完成的先期验证」：
快照不含本次调用自己的产出；纯字符估算偏低约 17%，必须用真实 input_tokens 兜底并单列残差。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

CJK_TOKENS_PER_CHAR = 0.6
OTHER_TOKENS_PER_CHAR = 0.3

BLOCK_FIXED_INSTRUCTIONS = "fixed_instructions"
BLOCK_SKILL_CATALOG = "skill_catalog"
BLOCK_HISTORY = "history"
BLOCK_TOOL_OUTPUT = "tool_output"
BLOCK_UNATTRIBUTED = "unattributed"

# 前四个是有字符数可查的块；unattributed 是残差，没有字符数
BLOCKS = (BLOCK_FIXED_INSTRUCTIONS, BLOCK_SKILL_CATALOG, BLOCK_HISTORY, BLOCK_TOOL_OUTPUT)

TOOL_OUTPUT_TYPES = ("function_call_output", "custom_tool_call_output")


def split_chars(text: str) -> tuple[int, int]:
    """按脚本类型切分字符数，返回 (CJK 字符数, 其他字符数)。"""
    cjk = 0
    for char in text:
        code = ord(char)
        if 0x3000 <= code <= 0x303F or 0x4E00 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF:
            cjk += 1
    return cjk, len(text) - cjk


@dataclass(frozen=True)
class BlockChars:
    cjk: int = 0
    other: int = 0

    @property
    def estimated_tokens(self) -> float:
        return self.cjk * CJK_TOKENS_PER_CHAR + self.other * OTHER_TOKENS_PER_CHAR


@dataclass(frozen=True)
class CallBreakdown:
    file_path: str
    ordinal: int
    session_id: str | None
    turn_id: str | None
    input_tokens: int
    blocks: dict[str, BlockChars]
    unattributed_tokens: float


def attribute(
    blocks: dict[str, BlockChars], input_tokens: int
) -> tuple[dict[str, float], float]:
    """把估算值锚定到真实 input_tokens。

    估算不超过真实值时原样保留、余量记为未归因；超过时四块等比缩小、未归因记 0。
    不变量：sum(各块) + 未归因 == input_tokens（input_tokens <= 0 时全 0）。
    """
    if input_tokens <= 0:
        return ({name: 0.0 for name in blocks}, 0.0)
    estimated = {name: block.estimated_tokens for name, block in blocks.items()}
    total = sum(estimated.values())
    if total <= 0:
        return ({name: 0.0 for name in blocks}, float(input_tokens))
    if total <= input_tokens:
        return (estimated, float(input_tokens) - total)
    scale = input_tokens / total
    return ({name: value * scale for name, value in estimated.items()}, 0.0)


def _payload_text(payload: dict) -> str:
    """取条目 payload 里会进上下文的文本。"""
    parts: list[str] = []
    for key in ("content", "summary"):
        for entry in payload.get(key) or []:
            if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                parts.append(entry["text"])
    for key in ("arguments", "output"):
        if isinstance(payload.get(key), str):
            parts.append(payload[key])
    return "\n".join(parts)


def _empty() -> dict[str, BlockChars]:
    return {name: BlockChars() for name in BLOCKS}


def _add(target: dict[str, BlockChars], block: str, text: str) -> None:
    cjk, other = split_chars(text)
    current = target[block]
    target[block] = BlockChars(cjk=current.cjk + cjk, other=current.other + other)


def _commit(committed: dict[str, BlockChars], buffer: dict[str, BlockChars]) -> None:
    for name in BLOCKS:
        held = buffer[name]
        current = committed[name]
        committed[name] = BlockChars(current.cjk + held.cjk, current.other + held.other)
        buffer[name] = BlockChars()


def decompose_lines(lines: Iterable[str], *, file_path: str) -> list[CallBreakdown]:
    committed = _empty()
    buffer = _empty()
    session_id: str | None = None
    results: list[CallBreakdown] = []

    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        if row.get("type") == "session_meta":
            session_id = payload.get("session_id") or session_id
            base = payload.get("base_instructions")
            if isinstance(base, dict):
                base = base.get("text")
            if isinstance(base, str):
                committed[BLOCK_FIXED_INSTRUCTIONS] = BlockChars(*split_chars(base))
        elif row.get("type") == "response_item":
            item_type = payload.get("type")
            text = _payload_text(payload)
            if item_type == "message" and payload.get("role") == "developer":
                _add(committed, BLOCK_SKILL_CATALOG, text)
            elif item_type == "message" and payload.get("role") == "user":
                _add(committed, BLOCK_HISTORY, text)
            elif item_type in TOOL_OUTPUT_TYPES:
                _commit(committed, buffer)
                _add(committed, BLOCK_TOOL_OUTPUT, text)
            else:
                # reasoning / message(assistant) / function_call / custom_tool_call / agent_message
                _add(buffer, BLOCK_HISTORY, text)
        elif row.get("type") == "token_usage_record":
            usage = payload.get("usage") or {}
            try:
                input_tokens = int(usage.get("input_tokens") or 0)
            except (TypeError, ValueError):
                input_tokens = 0
            ordinal = row.get("ordinal")
            if not isinstance(ordinal, int):
                continue
            snapshot = {name: BlockChars(b.cjk, b.other) for name, b in committed.items()}
            _, unattributed = attribute(snapshot, input_tokens)
            results.append(
                CallBreakdown(
                    file_path=file_path,
                    ordinal=ordinal,
                    session_id=payload.get("session_id") or session_id,
                    turn_id=payload.get("turn_id"),
                    input_tokens=input_tokens,
                    blocks=snapshot,
                    unattributed_tokens=unattributed,
                )
            )

    return results


def decompose_file(path: Path) -> list[CallBreakdown]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return decompose_lines(handle, file_path=str(path))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_context_decompose.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 真实数据 sanity 检查（不是自动化测试，但必须做并贴进 ledger）**

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run python -c "
from pathlib import Path
from agent_lens import context
files = sorted(Path.home().glob('.codex/sessions/**/*.jsonl'))
ratios = []
totals = {b: 0.0 for b in context.BLOCKS}
for p in files:
    for call in context.decompose_file(p):
        est = sum(b.estimated_tokens for b in call.blocks.values())
        if est > 0 and call.input_tokens > 0:
            ratios.append(call.input_tokens / est)
        for name, block in call.blocks.items():
            totals[name] += block.estimated_tokens
ratios.sort()
print('calls', len(ratios), 'median', round(ratios[len(ratios)//2], 3))
grand = sum(totals.values())
print({k: round(v / grand, 3) for k, v in totals.items()})
"
```
Expected: `median` 落在 1.0 到 1.6；四块占比都非零，`fixed_instructions` 约 3% 到 6%。
若 `fixed_instructions` 为 0，说明退回了「base_instructions 是字符串」的假设，回到先期验证第 3 条。

- [ ] **Step 6: Commit**

```bash
git add src/agent_lens/context.py tests/test_context_decompose.py
git commit -m "feat: replay api_call inputs into a five-block context breakdown"
```

---

## Task 2: schema v4 与幂等写入

**Files:**
- Modify: `src/agent_lens/schema.sql`（追加 `context_breakdown` 表）
- Modify: `src/agent_lens/storage.py`（`SCHEMA_VERSION` 3 → 4；新增 `write_context_breakdown`）
- Test: `tests/test_storage_context.py`
- Modify: `tests/test_schema_v3.py`（若其中硬编码了 `== 3`，改成引用 `storage.SCHEMA_VERSION`）

**Interfaces:**

- Consumes: Task 1 的 `CallBreakdown`、`BLOCKS`、`BLOCK_UNATTRIBUTED`、`attribute`
- Produces:
  - `storage.SCHEMA_VERSION == 4`
  - `storage.write_context_breakdown(conn, rows: Sequence[CallBreakdown]) -> int`：每个调用写 5 行，返回写入行数；同一批重复调用结果不变
  - 表 `context_breakdown`，主键 `(file_path, ordinal, block)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_context.py
from agent_lens import context, storage


def _sample() -> context.CallBreakdown:
    return context.CallBreakdown(
        file_path="f.jsonl", ordinal=3, session_id="s1", turn_id="t1", input_tokens=1000,
        blocks={
            context.BLOCK_FIXED_INSTRUCTIONS: context.BlockChars(other=100),
            context.BLOCK_SKILL_CATALOG: context.BlockChars(other=10),
            context.BLOCK_HISTORY: context.BlockChars(cjk=50, other=50),
            context.BLOCK_TOOL_OUTPUT: context.BlockChars(other=100),
        },
        unattributed_tokens=895.0,
    )


def test_schema_version_is_four(lens_db):
    assert storage.SCHEMA_VERSION == 4
    assert lens_db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_write_context_breakdown_is_idempotent(lens_db):
    assert storage.write_context_breakdown(lens_db, [_sample()]) == 5
    assert storage.write_context_breakdown(lens_db, [_sample()]) == 5
    rows = lens_db.execute(
        "SELECT block, attributed_tokens FROM context_breakdown ORDER BY block"
    ).fetchall()
    assert len(rows) == 5
    assert round(sum(row["attributed_tokens"] for row in rows), 6) == 1000.0


def test_unattributed_row_carries_no_chars(lens_db):
    storage.write_context_breakdown(lens_db, [_sample()])
    row = lens_db.execute(
        "SELECT cjk_chars, other_chars, attributed_tokens FROM context_breakdown WHERE block = ?",
        (context.BLOCK_UNATTRIBUTED,),
    ).fetchone()
    assert (row["cjk_chars"], row["other_chars"]) == (0, 0)
    assert row["attributed_tokens"] == 895.0


def test_migrating_a_v3_database_adds_the_table(tmp_path):
    conn = storage.connect(tmp_path / "old.db")
    conn.executescript(storage.SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    storage.init_db(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "context_breakdown" in tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_storage_context.py -v`
Expected: FAIL，`AttributeError: module 'agent_lens.storage' has no attribute 'write_context_breakdown'`

- [ ] **Step 3: Write minimal implementation**

在 `src/agent_lens/schema.sql` 末尾追加：

```sql
-- P2.1：上下文构成投影。正文不入库，这里只存字符数与锚定后的 token。
-- unattributed 块的 cjk_chars / other_chars 恒为 0：它是真实 input_tokens 与
-- 字符估算之间的差额（每次调用重发的工具定义与请求框架）。
CREATE TABLE IF NOT EXISTS context_breakdown (
    file_path         TEXT NOT NULL,
    ordinal           INTEGER NOT NULL,
    block             TEXT NOT NULL,
    session_id        TEXT,
    turn_id           TEXT,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    cjk_chars         INTEGER NOT NULL DEFAULT 0,
    other_chars       INTEGER NOT NULL DEFAULT 0,
    estimated_tokens  REAL NOT NULL DEFAULT 0,
    attributed_tokens REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, ordinal, block)
);

CREATE INDEX IF NOT EXISTS idx_context_breakdown_session
    ON context_breakdown(session_id);
```

`src/agent_lens/storage.py`：`SCHEMA_VERSION = 3` → `SCHEMA_VERSION = 4`；
导入区加 `from collections.abc import Sequence` 与 `from .context import CallBreakdown`（放在 `from .models import ParsedSession` 旁边）；
在 `counts` 之前新增：

```python
def write_context_breakdown(conn: sqlite3.Connection, rows: Sequence[CallBreakdown]) -> int:
    """写入上下文分解。幂等：同一批数据重复写不产生重复行。

    每个调用写 5 行（4 个字符块 + 1 个未归因块）。attributed_tokens 现算，
    保证该调用 5 行之和恰好等于 input_tokens。
    """
    from . import context as context_module

    written = 0
    with conn:
        for call in rows:
            attributed, unattributed = context_module.attribute(call.blocks, call.input_tokens)
            payload: list[tuple] = []
            for name in context_module.BLOCKS:
                block = call.blocks[name]
                payload.append(
                    (call.file_path, call.ordinal, name, call.session_id, call.turn_id,
                     call.input_tokens, block.cjk, block.other, block.estimated_tokens,
                     attributed[name])
                )
            payload.append(
                (call.file_path, call.ordinal, context_module.BLOCK_UNATTRIBUTED,
                 call.session_id, call.turn_id, call.input_tokens, 0, 0, 0.0, unattributed)
            )
            conn.executemany(
                """
                INSERT INTO context_breakdown (
                    file_path, ordinal, block, session_id, turn_id, input_tokens,
                    cjk_chars, other_chars, estimated_tokens, attributed_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (file_path, ordinal, block) DO UPDATE SET
                    session_id = excluded.session_id,
                    turn_id = excluded.turn_id,
                    input_tokens = excluded.input_tokens,
                    cjk_chars = excluded.cjk_chars,
                    other_chars = excluded.other_chars,
                    estimated_tokens = excluded.estimated_tokens,
                    attributed_tokens = excluded.attributed_tokens
                """,
                payload,
            )
            written += len(payload)
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_storage_context.py tests/test_schema_v3.py -v`
然后跑全量确认无回归：`UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest -q`

- [ ] **Step 5: Commit**

```bash
git add src/agent_lens/schema.sql src/agent_lens/storage.py tests/test_storage_context.py tests/test_schema_v3.py
git commit -m "feat: add schema v4 context_breakdown table with idempotent writes"
```

---

## Task 3: `agent-lens analyze` 命令

**Files:**
- Modify: `src/agent_lens/cli.py`
- Test: `tests/test_cli_analyze.py`

**Interfaces:**

- Consumes: `context.decompose_file`、`storage.write_context_breakdown`
- Produces: 子命令 `analyze`，参数 `--db`、`--sessions-dir`、`--file`（可重复）；标准输出一行
  `analyzed=<文件数> calls=<调用数> blocks=<写入行数>`；退出码 0

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_analyze.py
import json

from agent_lens.cli import main
from agent_lens.storage import connect, init_db


def _write_session(tmp_path):
    path = tmp_path / "sessions" / "rollout-2026-09-29T00-00-00-01a0abcd-1111-7222-8333-444455556666.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "session_meta",
         "payload": {"session_id": "s1", "base_instructions": {"text": "BASE"}}},
        {"type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "hi"}]}},
        {"type": "token_usage_record", "ordinal": 2,
         "payload": {"session_id": "s1", "turn_id": "t1", "usage": {"input_tokens": 500}}},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def test_analyze_writes_breakdown_rows(tmp_path, capsys):
    _write_session(tmp_path)
    db = tmp_path / "lens.db"
    assert main(["analyze", "--db", str(db), "--sessions-dir", str(tmp_path / "sessions")]) == 0
    out = capsys.readouterr().out
    assert "calls=1" in out and "blocks=5" in out

    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM context_breakdown").fetchone()[0] == 5
    summed = conn.execute("SELECT SUM(attributed_tokens) FROM context_breakdown").fetchone()[0]
    assert round(summed, 6) == 500.0


def test_analyze_is_idempotent(tmp_path):
    _write_session(tmp_path)
    db = tmp_path / "lens.db"
    for _ in range(2):
        assert main(["analyze", "--db", str(db), "--sessions-dir", str(tmp_path / "sessions")]) == 0
    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM context_breakdown").fetchone()[0] == 5


def test_analyze_single_file(tmp_path, capsys):
    path = _write_session(tmp_path)
    assert main(["analyze", "--db", str(tmp_path / "lens.db"), "--file", str(path)]) == 0
    assert "calls=1" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_cli_analyze.py -v`
Expected: FAIL，argparse 报 `invalid choice: 'analyze'`

- [ ] **Step 3: Write minimal implementation**

在 `build_parser()` 的 `serve` 之后加：

```python
    analyze = sub.add_parser("analyze", help="重放会话文件，计算上下文构成")
    analyze.add_argument("--config", default=None)
    analyze.add_argument("--db", default=None)
    analyze.add_argument("--sessions-dir", default=None)
    analyze.add_argument("--file", action="append", default=None, help="只分析指定文件，可重复")
```

`main()` 的 dispatch 里加 `if args.command == "analyze": return _run_analyze(args)`，并新增：

```python
def _run_analyze(args: argparse.Namespace) -> int:
    """重放会话文件并写入上下文分解。不联网、不改事实源。"""
    from . import context as context_module

    config, conn = _build_runtime(args)
    try:
        if args.file:
            paths = [Path(item) for item in args.file]
        else:
            paths = sorted(Path(config.sessions_dir).rglob("*.jsonl"))
        calls = 0
        written = 0
        for path in paths:
            breakdowns = context_module.decompose_file(path)
            if not breakdowns:
                continue
            calls += len(breakdowns)
            written += write_context_breakdown(conn, breakdowns)
        print(f"analyzed={len(paths)} calls={calls} blocks={written}")
        return 0
    finally:
        conn.close()
```

同时把 `write_context_breakdown` 加进 `cli.py` 里 `storage` 的导入列表。

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_cli_analyze.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 对真实数据跑一次并把输出贴进 ledger**

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run agent-lens analyze --db /private/tmp/p21.db
UV_CACHE_DIR="$PWD/.uv-cache" uv run python -c "
import sqlite3
conn = sqlite3.connect('/private/tmp/p21.db'); conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT block, SUM(attributed_tokens) AS t FROM context_breakdown GROUP BY block ORDER BY t DESC').fetchall()
total = sum(r['t'] for r in rows)
for r in rows: print(r['block'], round(r['t']), format(r['t'] / total, '.1%'))
"
```
Expected: 五块都有数，`history` 与 `tool_output` 合计约 90%，`unattributed` 约 15% 到 20%。

- [ ] **Step 6: Commit**

```bash
git add src/agent_lens/cli.py tests/test_cli_analyze.py
git commit -m "feat: add the analyze command that populates context breakdown"
```

---

## Task 4: 查询层与 API

**Files:**
- Modify: `src/agent_lens/queries.py`
- Create: `src/agent_lens/api/routes/context.py`
- Modify: `src/agent_lens/api/app.py`（注册路由）
- Modify: `src/agent_lens/api/schemas.py`（重导出新模型）
- Test: `tests/test_queries_context.py`
- Modify: `tests/test_api_endpoints.py`（新增一条 `/api/context` 用例）

**Interfaces:**

- Consumes: `context_breakdown` 表、既有的 `_range_bounds` / `_range_info` / `_fetch_api_rows` / `_row_tokens` / `_cache_hit_rate`
- Produces:
  - `queries.ContextBlockStat(block, tokens, share, cjk_chars, other_chars, estimated_tokens)`
  - `queries.ContextTrendPoint(day, block, tokens)`
  - `queries.ContextOverviewResponse(range, blocks, trend, input_tokens, analyzed_calls, total_calls, coverage, cache_hit_rate)`
  - `queries.context_overview(conn, *, days=30, project=None, now=None) -> ContextOverviewResponse`
  - `GET /api/context?days=&project=` 返回上面的模型

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queries_context.py
import pytest

from agent_lens import queries, storage
from agent_lens.api.app import create_app
from agent_lens.context import BLOCKS, BLOCK_UNATTRIBUTED, BlockChars, CallBreakdown


def _seed(conn) -> None:
    storage.write_context_breakdown(
        conn,
        [
            CallBreakdown(
                file_path="f1.jsonl", ordinal=1, session_id="s1", turn_id="t1",
                input_tokens=1000,
                blocks={name: BlockChars(other=100) for name in BLOCKS},
                unattributed_tokens=880.0,
            ),
            CallBreakdown(
                file_path="f1.jsonl", ordinal=2, session_id="s1", turn_id="t2",
                input_tokens=2000,
                blocks={name: BlockChars(other=100) for name in BLOCKS},
                unattributed_tokens=1880.0,
            ),
        ],
    )


def test_context_overview_sums_blocks(lens_db):
    _seed(lens_db)
    result = queries.context_overview(lens_db, days=3650)
    by_block = {stat.block: stat for stat in result.blocks}
    assert by_block[BLOCK_UNATTRIBUTED].tokens == pytest.approx(2760.0)
    assert sum(stat.tokens for stat in result.blocks) == pytest.approx(3000.0)
    assert result.analyzed_calls == 2


def test_context_endpoint_returns_blocks(lens_db, tmp_path):
    from fastapi.testclient import TestClient

    _seed(lens_db)
    db_file = lens_db.execute("PRAGMA database_list").fetchone()[2]
    with TestClient(create_app(db_file)) as client:
        response = client.get("/api/context?days=3650")
    assert response.status_code == 200
    blocks = {row["block"]: row for row in response.json()["blocks"]}
    assert blocks[BLOCK_UNATTRIBUTED]["tokens"] == pytest.approx(2760.0)
```

> 注：`/api/context` 的时间过滤靠 `context_breakdown` 关联 `api_calls` 的时间戳。上面两条测试只写入
> `context_breakdown` 而没有 `api_calls`，所以实现里必须让「无时间戳的分解行」在 `days` 很大时仍然被计入
> （`days=3650` 覆盖不到时请改用 `COALESCE(timestamp, turn.started_at)` 之外的兜底：分解行自身没有时间列，
> 因此实现应先从 `api_calls` 取范围内的 `(file_path, ordinal)` 白名单，再用白名单过滤分解行；
> 当日志为空时白名单为空、结果为 0 —— 这正是 `total_calls == 0` 时 `coverage = 0` 的原因）。
> 若 `test_context_endpoint_returns_blocks` 因为「没有 api_calls 行」而返回 0，请在 `_seed` 里一并
> 用 `write_parsed_session` 造一条同名的 `api_calls` 记录（ordinal 1、2，时间戳用 `SEEDED_NOW`），
> 这样白名单过滤与时间范围都能成立，断言保持不变。

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_queries_context.py -v`
Expected: FAIL，`AttributeError: module 'agent_lens.queries' has no attribute 'context_overview'`

- [ ] **Step 3: Write minimal implementation**

在 `queries.py` 顶部从 context 导入常量：`from .context import BLOCK_UNATTRIBUTED, BLOCKS as BLOCK_ORDER`；
追加：

```python
class ContextBlockStat(BaseModel):
    block: str
    tokens: float
    share: float
    cjk_chars: int = 0
    other_chars: int = 0
    estimated_tokens: float = 0.0


class ContextTrendPoint(BaseModel):
    day: str
    block: str
    tokens: float


class ContextOverviewResponse(BaseModel):
    range: RangeInfo
    blocks: list[ContextBlockStat]
    trend: list[ContextTrendPoint] = Field(default_factory=list)
    input_tokens: int
    analyzed_calls: int
    total_calls: int
    coverage: float
    cache_hit_rate: float


def context_overview(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    now: datetime | None = None,
) -> ContextOverviewResponse:
    """上下文构成：按块聚合，并曝光分析覆盖率。"""
    start, end = _range_bounds(now, days)
    api_rows = _fetch_api_rows(conn, start, end, project=project)
    total_calls = len(api_rows)
    allowed = {(row["file_path"], row["ordinal"]) for row in api_rows}
    input_tokens = sum(_row_tokens(row, "input_tokens") for row in api_rows)
    cached = sum(_row_tokens(row, "cached_input_tokens") for row in api_rows)

    totals: dict[str, float] = {}
    chars: dict[str, list[int]] = {}
    estimates: dict[str, float] = {}
    analyzed = 0
    if allowed:
        rows = conn.execute(
            """
            SELECT file_path, ordinal, block, cjk_chars, other_chars,
                   estimated_tokens, attributed_tokens
            FROM context_breakdown
            """
        ).fetchall()
        seen: set[tuple[str, int]] = set()
        for row in rows:
            key = (row["file_path"], row["ordinal"])
            if key not in allowed:
                continue
            seen.add(key)
            block = row["block"]
            totals[block] = totals.get(block, 0.0) + (row["attributed_tokens"] or 0.0)
            slot = chars.setdefault(block, [0, 0])
            slot[0] += row["cjk_chars"] or 0
            slot[1] += row["other_chars"] or 0
            estimates[block] = estimates.get(block, 0.0) + (row["estimated_tokens"] or 0.0)
        analyzed = len(seen)

    attributed_total = sum(totals.values())
    blocks = [
        ContextBlockStat(
            block=name,
            tokens=totals.get(name, 0.0),
            share=(totals.get(name, 0.0) / attributed_total) if attributed_total else 0.0,
            cjk_chars=chars.get(name, [0, 0])[0],
            other_chars=chars.get(name, [0, 0])[1],
            estimated_tokens=estimates.get(name, 0.0),
        )
        for name in (*BLOCK_ORDER, BLOCK_UNATTRIBUTED)
    ]
    return ContextOverviewResponse(
        range=_range_info(start, end, days),
        blocks=blocks,
        trend=[],
        input_tokens=input_tokens,
        analyzed_calls=analyzed,
        total_calls=total_calls,
        coverage=analyzed / total_calls if total_calls else 0.0,
        cache_hit_rate=_cache_hit_rate(cached, input_tokens),
    )
```

创建 `src/agent_lens/api/routes/context.py`：

```python
"""上下文成本页的聚合接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import ContextOverviewResponse

router = APIRouter(prefix="/api", tags=["context"])


@router.get("/context", response_model=ContextOverviewResponse)
def context(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> ContextOverviewResponse:
    return queries.context_overview(conn, days=days, project=project)
```

`api/schemas.py` 的 `from ..queries import (...)` 里加 `ContextBlockStat`、`ContextOverviewResponse`、
`ContextTrendPoint`；`api/app.py` 的 `from .routes import (...)` 加 `context`，并在 `create_app` 的路由元组里
把 `context` 排进 `for module in (...)`（顺序放在 `overview` 之后即可）。注意 `context` 这个名字会与局部变量
冲突时改用 `from .routes import context as context_routes`。

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest tests/test_queries_context.py tests/test_api_endpoints.py -v`
然后 `uv run pytest -q` 与 `uv run ruff check src tests` 都要干净。

- [ ] **Step 5: Commit**

```bash
git add src/agent_lens/queries.py src/agent_lens/api tests/test_queries_context.py tests/test_api_endpoints.py
git commit -m "feat: expose context breakdown aggregates over the api"
```

---

## Task 5: 前端「上下文成本」页

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/endpoints.ts`
- Create: `web/src/views/ContextView.vue`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/layout/SideNav.vue`
- Test: `npm run build` + `scripts/nav-smoke.mjs`

**Interfaces:**

- Consumes: `GET /api/context`（snake_case，与 `ContextOverviewResponse` 逐字一致）
- Produces: 路由 `/context`、侧栏导航项「上下文成本」、构成环形图 + 明细表 + 覆盖率卡片

- [ ] **Step 1: 类型与端点**

`web/src/api/types.ts` 追加：

```ts
export interface ContextBlockStat {
  block: string
  tokens: number
  share: number
  cjk_chars: number
  other_chars: number
  estimated_tokens: number
}

export interface ContextTrendPoint {
  day: string
  block: string
  tokens: number
}

export interface ContextOverviewResponse {
  range: { start: string; end: string; days: number }
  blocks: ContextBlockStat[]
  trend: ContextTrendPoint[]
  input_tokens: number
  analyzed_calls: number
  total_calls: number
  coverage: number
  cache_hit_rate: number
}
```

`web/src/api/endpoints.ts` 的 `api` 对象加：

```ts
  context: (days: number, project?: string) =>
    http.get<ContextOverviewResponse>(`/context${query({ days, project })}`),
```

- [ ] **Step 2: 页面**

`web/src/views/ContextView.vue` 照 `views/ToolsView.vue` 的骨架写（`PageHeader` + `StateBlock` +
`MetricCard` + `ChartCard` + `EChart` + `useAsync` + `usePolling` + `useRange` + `useTheme`）：

```vue
<template>
  <PageHeader
    title="上下文成本"
    description="把每次调用的 input 拆开看：固定指令、skill 目录、历史对话、工具输出，以及未归因部分"
  />
  <StateBlock
    :loading="state.loading.value && !data"
    :error="state.error.value"
    :empty="!data || data.analyzed_calls === 0"
    empty-text="还没有上下文分解数据，先运行 agent-lens analyze"
    @retry="state.reload"
  >
    <div class="cards">
      <MetricCard label="分析覆盖率" :value="formatPercent(data?.coverage ?? 0)"
                  :hint="`${data?.analyzed_calls ?? 0} / ${data?.total_calls ?? 0} 次调用`" />
      <MetricCard label="cache 命中率" :value="formatPercent(data?.cache_hit_rate ?? 0)"
                  hint="命中部分实际成本远低于数字本身" />
      <MetricCard label="input token 总量" :value="formatTokens(data?.input_tokens ?? 0)"
                  hint="五块之和等于这个数" />
    </div>
    <ChartCard title="上下文构成" subtitle="锚定后的 token；未归因是每次重发的工具定义与请求框架">
      <EChart :option="pieOption" />
    </ChartCard>
    <div class="al-card table">
      <el-table :data="data?.blocks ?? []" size="small">
        <el-table-column label="块" :formatter="(row) => BLOCK_LABELS[row.block] ?? row.block" />
        <el-table-column label="token" :formatter="(row) => formatInt(row.tokens)" />
        <el-table-column label="占比" :formatter="(row) => formatPercent(row.share)" />
        <el-table-column label="CJK 字符" :formatter="(row) => formatInt(row.cjk_chars)" />
        <el-table-column label="其他字符" :formatter="(row) => formatInt(row.other_chars)" />
      </el-table>
    </div>
  </StateBlock>
</template>
```

脚本部分：

```ts
const { days } = useRange()
const { theme } = useTheme()
const state = useAsync(() => api.context(days.value))
usePolling(state.reload)
const data = computed(() => state.data.value ?? null)
const BLOCK_LABELS: Record<string, string> = {
  fixed_instructions: '固定指令',
  skill_catalog: 'skill 目录',
  history: '历史对话',
  tool_output: '工具输出',
  unattributed: '未归因',
}
const pieOption = computed<EChartsCoreOption>(() => ({
  tooltip: { trigger: 'item' },
  series: [{
    type: 'pie',
    radius: ['45%', '70%'],
    data: (data.value?.blocks ?? []).map((row) => ({
      name: BLOCK_LABELS[row.block] ?? row.block,
      value: row.tokens,
    })),
  }],
}))
```

颜色用 `utils/palette.ts` 里已有的 `CHART_COLORS`，不要新造色板。若表格里要显示颜色，用同一组。

- [ ] **Step 3: 路由与导航**

`router/index.ts` 在 `/tools` 之后插入：

```ts
    {
      path: '/context',
      name: 'context',
      component: () => import('@/views/ContextView.vue'),
      meta: { title: '上下文成本' },
    },
```

`SideNav.vue` 照同文件既有导航项加一条 `to="/context"`、文字「上下文成本」的项，图标用该文件已导入的图标之一。

- [ ] **Step 4: 构建 + 浏览器验收**

```bash
cd web && npm run build
```
Expected: 构建成功（含 `vue-tsc --noEmit`）。

然后（需要能监听端口的机器，本仓库 agent 沙箱不行）：

```bash
uv run agent-lens analyze --db /private/tmp/p21.db
uv run agent-lens serve --db /private/tmp/p21.db --port 8000
BASE=http://127.0.0.1:8000 node scripts/nav-smoke.mjs
```
Expected: 既有 7 步全过；**执行者必须在报告里贴探针输出**（同时是 KI-1 的回归证据）。

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat: add the context cost view to the dashboard"
```

---

## Self-Review（控制器已核对）

- **Spec 覆盖**：设计文档 6.7 的四块 → Task 1 的四个 `BLOCK_*`；「必须与 cache 命中率联合呈现」→
  Task 4 的 `cache_hit_rate` 与 Task 5 的卡片；9.1 的「上下文成本」页 → Task 5；6.8 skill 命中属 P2.2，
  6.9 模型对比属 P2.3，本计划有意不覆盖。
- **类型一致性**：`CallBreakdown.blocks` 的键只来自 `context.BLOCKS`（四块）；`unattributed` 只以数值出现，
  任何地方都不要往 `blocks` 里塞它。
- **不变量**：每个调用的 5 行 `attributed_tokens` 之和 == `input_tokens`。Task 2、Task 3、Task 4 各有一处断言守着。
- **已知取舍**：`trend` 在 P2.1 返回空列表（响应模型保留字段），本页只要求构成占比；按天趋势留给 P3.2。
