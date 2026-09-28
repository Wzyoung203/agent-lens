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
import threading
import time
from bisect import bisect_right
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

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


def backoff_delay(
    attempt: int, *, base: float = 1.0, factor: float = 2.0, max_delay: float = 300.0
) -> float:
    """指数退避：1 秒起，每次翻倍，上限 5 分钟（设计文档 10 节）。"""
    attempt = max(attempt, 1)
    return min(base * (factor ** (attempt - 1)), max_delay)


class RateLimiter:
    """令牌间隔限速器。

    设计文档 4.9 节的硬上限是 1000 请求/分钟，超过会被 Langfuse 拒绝，
    所以 Reporter 每次发送前先 acquire。用单调时钟，不受系统时间调整影响。
    """

    def __init__(self, max_per_minute: int, *, clock: Callable[[], float] = time.monotonic):
        self._interval = 60.0 / max(1, max_per_minute)
        self._clock = clock
        self._next_allowed = 0.0

    def acquire(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        wait = self._next_allowed - self._clock()
        if wait > 0:
            sleep(wait)
        self._next_allowed = max(self._clock(), self._next_allowed) + self._interval


class TraceBackend(Protocol):
    """上报后端的最小契约。Sink 只依赖这个协议，不依赖任何 SDK 类型。"""

    def start_trace(
        self, *, trace_id: str, session_id: str, name: str, metadata: dict[str, Any]
    ) -> None: ...

    def add_generation(
        self,
        *,
        trace_id: str,
        name: str,
        model: str | None,
        usage: dict[str, int] | None,
        metadata: dict[str, Any],
    ) -> None: ...

    def add_span(self, *, trace_id: str, name: str, metadata: dict[str, Any]) -> None: ...

    def flush(self) -> None: ...


class RecordingBackend:
    """把后端调用记下来。单测与端到端冒烟用它替代真实 Langfuse。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start_trace(self, **kwargs: Any) -> None:
        self.calls.append(("start_trace", kwargs))

    def add_generation(self, **kwargs: Any) -> None:
        self.calls.append(("add_generation", kwargs))

    def add_span(self, **kwargs: Any) -> None:
        self.calls.append(("add_span", kwargs))

    def flush(self) -> None:
        self.calls.append(("flush", {}))


class LangfuseTraceBackend:
    """唯一直接接触 langfuse SDK 的地方：SDK 版本变化时只改这一个类。

    ⚠️ 真实 Langfuse 链路属于「阶段 1 手动验证一次」的范围（设计文档 11 节），
    自动化测试只覆盖到 TraceBackend 协议这一层（用 RecordingBackend）。
    这里用 v3 的 start_span / start_generation + trace_context 透传 trace_id；
    session_id 同时放进 metadata 与 update_current_trace，人工验证时若需要
    Langfuse 的 session 视图，只改本类即可。
    """

    def __init__(self, client: Any):
        self._client = client

    @classmethod
    def from_keys(cls, *, public_key: str, secret_key: str, host: str) -> LangfuseTraceBackend:
        from langfuse import Langfuse  # 延迟导入：不上报时不需要安装 langfuse

        return cls(Langfuse(public_key=public_key, secret_key=secret_key, host=host))

    def start_trace(
        self, *, trace_id: str, session_id: str, name: str, metadata: dict[str, Any]
    ) -> None:
        self._client.update_current_trace(
            name=name, session_id=session_id, metadata={"trace_id": trace_id, **metadata}
        )

    def add_generation(
        self,
        *,
        trace_id: str,
        name: str,
        model: str | None,
        usage: dict[str, int] | None,
        metadata: dict[str, Any],
    ) -> None:
        generation = self._client.start_generation(
            name=name,
            model=model,
            usage_details=usage,
            trace_context={"trace_id": trace_id},
            metadata=metadata,
        )
        generation.end()

    def add_span(self, *, trace_id: str, name: str, metadata: dict[str, Any]) -> None:
        span = self._client.start_span(
            name=name, trace_context={"trace_id": trace_id}, metadata=metadata
        )
        span.end()

    def flush(self) -> None:
        self._client.flush()


class Sink(Protocol):
    """上报出口。只有 send 一个方法：队列、限速与退避都由 Reporter 负责。"""

    def send(self, envelope: ReportEnvelope) -> None: ...


class NullSink:
    """不上报时的空实现。"""

    def send(self, envelope: ReportEnvelope) -> None:
        return None


class RecordingSink:
    """把收到的记录留在内存里，供本地验证与端到端测试使用。"""

    def __init__(self) -> None:
        self.received: list[ReportEnvelope] = []

    def send(self, envelope: ReportEnvelope) -> None:
        self.received.append(envelope)


class LangfuseSink:
    """把 ReportEnvelope 映射成 Langfuse 的 trace / generation / span（设计文档 6.2 节）。

    一个 turn 一条 trace；api_call 是 generation，tool_call 是 span。
    trace_id 由 (session_id, turn_id) 推导，因此重投不会散成多条 trace。
    """

    def __init__(self, backend: TraceBackend, *, enabled: bool = True):
        self.backend = backend
        self.enabled = enabled

    def send(self, envelope: ReportEnvelope) -> None:
        if not self.enabled:
            return
        metadata = {
            "session_id": envelope.session_id,
            "turn_id": envelope.turn_id,
            "granularity": envelope.granularity,
            "report_id": envelope.report_id,
        }
        self.backend.start_trace(
            trace_id=envelope.trace_id,
            session_id=envelope.session_id,
            name=f"turn:{envelope.turn_id or envelope.session_id}",
            metadata=metadata,
        )
        if envelope.kind == "api_call":
            self.backend.add_generation(
                trace_id=envelope.trace_id,
                name=f"generation:{envelope.payload.get('model') or 'unknown'}",
                model=envelope.payload.get("model"),
                usage=envelope.payload.get("usage"),
                metadata=metadata,
            )
        elif envelope.kind == "tool_call":
            self.backend.add_span(
                trace_id=envelope.trace_id,
                name=f"tool:{envelope.payload.get('name') or 'unknown'}",
                metadata={
                    **metadata,
                    "success": envelope.payload.get("success"),
                    "exit_code": envelope.payload.get("exit_code"),
                    "wall_time_seconds": envelope.payload.get("wall_time_seconds"),
                    "arguments_summary": envelope.payload.get("arguments_summary"),
                    "output_summary": envelope.payload.get("output_summary"),
                },
            )
        else:
            self.backend.add_span(
                trace_id=envelope.trace_id,
                name=f"turn:{envelope.turn_id}",
                metadata={**metadata, **envelope.payload},
            )


class ReportOutcome(BaseModel):
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    pending: int = 0


class Reporter:
    """出队 -> 限速 -> 发送 -> 记账。任何异常都不向上抛（设计文档 10 节）。"""

    def __init__(
        self,
        queue: ReportQueue,
        sink: Sink,
        *,
        batch_size: int = 50,
        max_requests_per_minute: int = 1000,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utc_now,
        backoff_base: float = 1.0,
        backoff_max: float = 300.0,
    ):
        self.queue = queue
        self.sink = sink
        self.batch_size = batch_size
        self._limiter = rate_limiter or RateLimiter(max_requests_per_minute)
        self._sleep = sleep
        self._now = now
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

    def drain_once(self) -> ReportOutcome:
        """取一批待发记录并逐条发送。失败的那条按退避推迟，且本轮立即停止。"""
        envelopes = self.queue.pending(limit=self.batch_size, now=self._now())
        sent = 0
        failed = 0
        for envelope in envelopes:
            self._limiter.acquire(sleep=self._sleep)
            try:
                self.sink.send(envelope)
            except Exception as error:  # noqa: BLE001 — 上报失败绝不能影响采集
                delay = backoff_delay(
                    envelope.attempts + 1, base=self._backoff_base, max_delay=self._backoff_max
                )
                self.queue.mark_failed(
                    envelope.report_id,
                    f"{type(error).__name__}: {error}",
                    next_attempt_at=self._now() + timedelta(seconds=delay),
                )
                failed += 1
                break
            else:
                self.queue.mark_sent(envelope.report_id)
                sent += 1
        return ReportOutcome(
            attempted=len(envelopes),
            sent=sent,
            failed=failed,
            pending=self.queue.outstanding(),
        )

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        idle_sleep: float = 5.0,
        max_rounds: int | None = None,
    ) -> int:
        """轮询出队。max_rounds 供测试用；stop_event 置位后停止。"""
        rounds = 0
        while True:
            outcome = self.drain_once()
            rounds += 1
            if max_rounds is not None and rounds >= max_rounds:
                return rounds
            if stop_event is not None and stop_event.is_set():
                return rounds
            if outcome.attempted == 0:
                self._sleep(idle_sleep)
