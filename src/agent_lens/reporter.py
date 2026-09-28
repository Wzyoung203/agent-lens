"""上报层：落盘队列 + 记录构造 + 批量发送 + 限速 + 指数退避。

与采集层解耦（设计文档 5.1 / 5.5 节）：
  * 队列就是 SQLite 里的 report_queue 表——进程被杀不丢数据，重启后自动重投；
  * 采集器只管入队，Reporter 只管出队发送，Langfuse 不可用不影响采集；
  * payload 入队前已脱敏（第 8 节），本模块不接触原始日志正文。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from bisect import bisect_right
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import ParsedSession, TokenUsage, ToolResultRecord, TurnContextRecord
from .redact import DEFAULT_SUMMARY_CHARS, Redactor
from .storage import to_iso

ReportKind = Literal["api_call", "tool_call", "turn"]
Granularity = Literal["full", "turn"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def derive_trace_id(session_id: str, turn_id: str | None) -> str:
    """派生稳定的 32 位十六进制 trace id。

    同一个 (session, turn) 的 generation 与 span 必须落在同一个 trace 上，
    所以 id 必须由业务键推导，不能用随机数——否则重投会散成多条 trace。
    """
    seed = f"{session_id}:{turn_id or 'session'}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


class ReportEnvelope(BaseModel):
    """一条待上报记录。report_id 是幂等键，重复入队会被忽略。"""

    report_id: str
    session_id: str
    turn_id: str | None = None
    kind: ReportKind
    granularity: Granularity = "full"
    payload: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    created_at: datetime = Field(default_factory=_utc_now)

    @property
    def trace_id(self) -> str:
        return derive_trace_id(self.session_id, self.turn_id)


class ReportQueue:
    """基于 report_queue 表的持久队列。单写者，与采集器共用一个连接。"""

    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], datetime] = _utc_now):
        self.conn = conn
        self._now = now

    def enqueue_many(self, envelopes: Iterable[ReportEnvelope]) -> int:
        """返回真正新增的条数。已存在的 report_id 被忽略，因此可安全重放。"""
        now_iso = to_iso(self._now())
        inserted = 0
        with self.conn:
            for envelope in envelopes:
                cursor = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO report_queue (
                        report_id, session_id, turn_id, kind, granularity,
                        payload, status, attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                    """,
                    (
                        envelope.report_id,
                        envelope.session_id,
                        envelope.turn_id,
                        envelope.kind,
                        envelope.granularity,
                        json.dumps(envelope.payload, ensure_ascii=False),
                        to_iso(envelope.created_at),
                        now_iso,
                    ),
                )
                inserted += cursor.rowcount or 0
        return inserted

    def pending(self, *, limit: int, now: datetime | None = None) -> list[ReportEnvelope]:
        """取出到期的待发记录。

        包含两种状态：从未发送过的（pending）与发送失败等重试的（failed），
        只要 next_attempt_at 已到（或为空）就都算「待发」。退避窗口内的不返回。
        """
        moment = to_iso(now or self._now())
        rows = self.conn.execute(
            """
            SELECT * FROM report_queue
            WHERE status IN ('pending', 'failed')
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY created_at, report_id
            LIMIT ?
            """,
            (moment, limit),
        ).fetchall()
        return [
            ReportEnvelope(
                report_id=row["report_id"],
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                kind=row["kind"],
                granularity=row["granularity"],
                payload=json.loads(row["payload"]),
                attempts=row["attempts"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def mark_sent(self, report_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE report_queue SET status = 'sent', last_error = NULL, "
                "next_attempt_at = NULL, updated_at = ? WHERE report_id = ?",
                (to_iso(self._now()), report_id),
            )

    def mark_failed(self, report_id: str, error: str, *, next_attempt_at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE report_queue
                SET status = 'failed',
                    attempts = attempts + 1,
                    last_error = ?,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE report_id = ?
                """,
                (error[:500], to_iso(next_attempt_at), to_iso(self._now()), report_id),
            )

    def reset(self) -> None:
        """把失败的记录重新排队，供手动重投。"""
        with self.conn:
            self.conn.execute(
                "UPDATE report_queue SET status = 'pending', next_attempt_at = NULL, "
                "updated_at = ? WHERE status = 'failed'",
                (to_iso(self._now()),),
            )

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM report_queue GROUP BY status"
        ).fetchall()
        counts = {"pending": 0, "sent": 0, "failed": 0}
        for row in rows:
            counts[row["status"]] = int(row["n"])
        return counts

    def outstanding(self) -> int:
        """还没送出去的条数（含正在退避重试的）。"""
        counts = self.counts()
        return counts["pending"] + counts["failed"]


def _usage_payload(usage: TokenUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_write_input_tokens": usage.cache_write_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "total_tokens": usage.total_tokens,
    }


def _turn_attribution(parsed: ParsedSession) -> tuple[list[int], list[str | None]]:
    """把轮次归属算成「其后第一条 api_call 的轮次」的查表结构。

    response_item 行不带 turn_id（实测确认），只有 token_usage_record 带。实测行序是
    function_call -> function_call_output -> token_usage_record，所以工具调用之后的第一条
    api_call 与它同属一轮。这只影响 span 挂在哪个 trace 上，不影响任何 token 统计。
    """
    pairs = sorted(
        (call.ordinal, call.turn_id) for call in parsed.api_calls if call.turn_id is not None
    )
    return [ordinal for ordinal, _ in pairs], [turn_id for _, turn_id in pairs]


def _turn_for_ordinal(ordinals: list[int], turns: list[str | None], ordinal: int) -> str | None:
    if not ordinals:
        return None
    index = bisect_right(ordinals, ordinal)
    if index >= len(ordinals):
        index = len(ordinals) - 1
    return turns[index]


def _result_text(result: ToolResultRecord) -> str:
    return result.result_summary or result.output_text


def build_envelopes(
    parsed: ParsedSession,
    *,
    redactor: Redactor,
    granularity: Granularity = "full",
    summary_chars: int = DEFAULT_SUMMARY_CHARS,
) -> list[ReportEnvelope]:
    """把一批解析结果转成待上报记录（设计文档 6.2 节）。

    full：每个 api_call 一条 generation，每个 tool_call 一条 span；
    turn：每轮汇总成一条记录（配额兜底的降级形态，见设计文档 5.5 节）。
    """
    contexts: dict[str, TurnContextRecord] = {
        context.turn_id: context for context in parsed.turn_contexts if context.turn_id
    }
    if granularity == "turn":
        return _turn_envelopes(parsed, contexts)

    results_by_call = {
        result.call_id: result for result in parsed.tool_results if result.call_id
    }
    ordinals, turns = _turn_attribution(parsed)
    envelopes: list[ReportEnvelope] = []

    for call in parsed.api_calls:
        context = contexts.get(call.turn_id or "")
        envelopes.append(
            ReportEnvelope(
                report_id=f"api_call:{call.file_path}:{call.ordinal}",
                session_id=parsed.session_id,
                turn_id=call.turn_id,
                kind="api_call",
                granularity="full",
                payload={
                    "response_id": call.response_id,
                    "timestamp": call.timestamp.isoformat() if call.timestamp else None,
                    "model": context.model if context else None,
                    "effort": context.effort if context else None,
                    "usage": _usage_payload(call.usage),
                },
            )
        )

    for tool_call in parsed.tool_calls:
        result = results_by_call.get(tool_call.call_id or "")
        envelopes.append(
            ReportEnvelope(
                report_id=f"tool_call:{tool_call.file_path}:{tool_call.ordinal}",
                session_id=parsed.session_id,
                turn_id=_turn_for_ordinal(ordinals, turns, tool_call.ordinal),
                kind="tool_call",
                granularity="full",
                payload={
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "tool_kind": tool_call.kind,
                    "arguments_summary": redactor.summarize(
                        tool_call.arguments_raw, limit=summary_chars
                    ),
                    "output_summary": (
                        redactor.summarize(_result_text(result), limit=summary_chars)
                        if result
                        else None
                    ),
                    "exit_code": result.exit_code if result else None,
                    "success": result.success if result else None,
                    "wall_time_seconds": result.wall_time_seconds if result else None,
                },
            )
        )

    return envelopes


def _turn_envelopes(
    parsed: ParsedSession,
    contexts: dict[str, TurnContextRecord],
) -> list[ReportEnvelope]:
    """一轮一条记录：token 汇总 + 工具失败数 + 轮次耗时（配额兜底粒度）。"""
    ordinals, turns = _turn_attribution(parsed)
    results_by_call = {
        result.call_id: result for result in parsed.tool_results if result.call_id
    }
    grouped: dict[str | None, list] = {}
    for call in parsed.api_calls:
        grouped.setdefault(call.turn_id, []).append(call)

    envelopes: list[ReportEnvelope] = []
    for turn_id, calls in grouped.items():
        turn = next((item for item in parsed.turns if item.turn_id == turn_id), None)
        attributed_tools = [
            tool
            for tool in parsed.tool_calls
            if _turn_for_ordinal(ordinals, turns, tool.ordinal) == turn_id
        ]
        failures = 0
        for tool in attributed_tools:
            result = results_by_call.get(tool.call_id or "")
            if result is not None and result.success is False:
                failures += 1
        context = contexts.get(turn_id or "")
        envelopes.append(
            ReportEnvelope(
                report_id=f"turn:{parsed.file_path}:{turn_id}",
                session_id=parsed.session_id,
                turn_id=turn_id,
                kind="turn",
                granularity="turn",
                payload={
                    "model": context.model if context else None,
                    "effort": context.effort if context else None,
                    "api_call_count": len(calls),
                    "tool_call_count": len(attributed_tools),
                    "tool_failure_count": failures,
                    "total_input_tokens": sum(item.usage.input_tokens for item in calls),
                    "total_cached_input_tokens": sum(
                        item.usage.cached_input_tokens for item in calls
                    ),
                    "total_output_tokens": sum(item.usage.output_tokens for item in calls),
                    "duration_ms": turn.duration_ms if turn else None,
                    "aborted_reason": turn.aborted_reason if turn else None,
                },
            )
        )
    return envelopes
