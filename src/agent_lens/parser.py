"""把 Codex 的 JSONL 会话文件解析成结构化对象。

本模块只做解析：不写数据库、不发网络请求、不修改源文件。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

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


class LineParseResult(BaseModel):
    """单行的解析结果。每次最多命中一个有意义的字段。"""

    session_meta: SessionMetaRecord | None = None
    turn_context: TurnContextRecord | None = None
    api_call: ApiCallRecord | None = None
    tool_call: ToolCallRecord | None = None
    tool_result: ToolResultRecord | None = None
    item_completed: ItemCompletedRecord | None = None
    turn_started: TurnRecord | None = None
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
                "turn_started",
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

    ordinal = int(row.get("ordinal", ordinal))

    try:
        return _dispatch(row.get("type"), payload, file_path, ordinal)
    except Exception:  # noqa: BLE001 — 单行解析失败一律降级成 ParseError，不让一行坏数据中断整个文件
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
                started_at=epoch_to_utc(payload.get("started_at")),
                completed_at=epoch_to_utc(payload.get("completed_at")),
                duration_ms=payload.get("duration_ms"),
                time_to_first_token_ms=payload.get("time_to_first_token_ms"),
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


def session_id_from_filename(path: Path) -> str:
    """从文件名取会话 ID 兜底。parse_session_file 与 P1.3 采集器共用这一份实现。"""
    return _session_id_from_filename(path)


def _upsert_turn(parsed: ParsedSession, turn: TurnRecord) -> None:
    """按 turn_id 合并 task_complete 与 turn_aborted。"""
    for existing in parsed.turns:
        if existing.turn_id == turn.turn_id:
            existing.started_at = turn.started_at or existing.started_at
            existing.completed_at = turn.completed_at or existing.completed_at
            existing.duration_ms = turn.duration_ms or existing.duration_ms
            existing.time_to_first_token_ms = (
                turn.time_to_first_token_ms or existing.time_to_first_token_ms
            )
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
    for turn in (result.turn_started, result.turn_completed, result.turn_aborted):
        if turn is not None:
            _upsert_turn(parsed, turn)
    if result.event is not None:
        parsed.events.append(result.event)
    if result.parse_error is not None:
        parsed.parse_errors.append(result.parse_error)


def merge_line_result(parsed: ParsedSession, result: LineParseResult) -> None:
    """把单行解析结果合并进 ParsedSession。

    P1.3 的采集器按增量批次解析，需要复用与 parse_session_file 完全相同的合并逻辑，
    所以把内部的 _merge 公开出来；两者必须是同一份实现，不能各写一遍。
    """
    _merge(parsed, result)


def parse_session_file(path: Path) -> ParsedSession:
    """解析一个会话文件。

    容错策略：
      单行损坏        -> 记入 parse_errors，继续解析
      末尾半行        -> 存入 partial_tail，不计错误（补齐后重新解析）
      缺 session_meta -> 会话 ID 回退为文件名里的 UUID
    """
    file_path = str(path)
    parsed = ParsedSession(session_id=session_id_from_filename(path), file_path=file_path)
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
