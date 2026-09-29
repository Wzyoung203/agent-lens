"""把每次 API 调用的 input 重放成「上下文构成」（设计文档 6.7 节）。

正文不入库，所以分解只能回原始 JSONL 重放。本模块只做纯计算，不写库、不碰文件系统
（`decompose_file` 只读）。

两处关键口径都有实测依据（见计划文档「已完成的先期验证」）：
  1. `token_usage_record` 排在本次调用产出之后、工具输出之前，因此快照是「已提交上下文」，
     不含本次调用自己的 reasoning / function_call；
  2. 纯字符估算系统性偏低约 17%（工具定义与请求框架每次重发），所以必须用真实
     input_tokens 兜底，把余量单列成 unattributed 块。
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
