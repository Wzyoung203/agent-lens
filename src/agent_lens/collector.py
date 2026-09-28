"""增量采集：扫描会话目录 -> 读新增字节 -> 解析 -> 脱敏 -> 写库 -> 入上报队列。

职责边界（设计文档 5.1 节）：
  * Collector 只读 ~/.codex，绝不修改或删除原始文件；
  * 采集与上报解耦：采集器只往 report_queue 写待发记录，发送是 reporter.py 的事；
  * 幂等键 (file_path, ordinal) 来自日志行自带的 ordinal，重复扫描不产生重复行。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from .models import ParsedSession
from .parser import merge_line_result, parse_line, session_id_from_filename
from .redact import DEFAULT_SUMMARY_CHARS, Redactor
from .reporter import ReportEnvelope, ReportQueue, build_envelopes
from .storage import get_ingest_state, update_ingest_state, write_parsed_session


class IncrementResult(BaseModel):
    """一次增量读取的结果。

    lines 每项是 (行首字节偏移, 行文本)，行文本不含换行符。
    行首字节偏移是给「日志行缺少 ordinal」时的兜底幂等键用的：
    字节偏移在进程重启后依然稳定，进程内的行计数则不稳定。
    """

    lines: list[tuple[int, str]] = Field(default_factory=list)
    byte_offset: int = 0
    partial_tail: str | None = None


def read_increment(path: Path, byte_offset: int = 0) -> IncrementResult:
    """从 byte_offset 起读新增字节，只返回以 \\n 结束的完整行。

    文件正在被追加写入是常态（设计文档 4.5 节），所以末尾的半行必须退回去等下次；
    用二进制模式读，保证 byte_offset 是真正的字节位置——文本模式的 tell 在 UTF-8 下不可靠。
    """
    size = path.stat().st_size
    if byte_offset >= size:
        return IncrementResult(byte_offset=byte_offset)

    with path.open("rb") as handle:
        handle.seek(byte_offset)
        data = handle.read()

    end = data.rfind(b"\n")
    if end < 0:
        return IncrementResult(
            byte_offset=byte_offset,
            partial_tail=data.decode("utf-8", errors="replace"),
        )

    complete = data[: end + 1]
    remainder = data[end + 1 :]
    lines: list[tuple[int, str]] = []
    cursor = byte_offset
    for chunk in complete[:-1].split(b"\n"):
        if chunk.strip():
            lines.append((cursor, chunk.decode("utf-8", errors="replace")))
        cursor += len(chunk) + 1

    return IncrementResult(
        lines=lines,
        byte_offset=byte_offset + len(complete),
        partial_tail=remainder.decode("utf-8", errors="replace") if remainder else None,
    )


class CollectOutcome(BaseModel):
    """一次扫描的结果，用于日志与测试断言。"""

    scans: int = 0
    files_scanned: int = 0
    files_changed: int = 0
    files_truncated: int = 0
    lines_read: int = 0
    api_calls_written: int = 0
    tool_calls_written: int = 0
    parse_errors: int = 0
    reports_queued: int = 0


def _batch_max_ordinal(parsed: ParsedSession) -> int | None:
    """本批解析结果里的最大 ordinal，用于推进水位；空批次返回 None。"""
    ordinals = [call.ordinal for call in parsed.api_calls]
    ordinals += [call.ordinal for call in parsed.tool_calls]
    ordinals += [result.ordinal for result in parsed.tool_results]
    ordinals += [item.ordinal for item in parsed.items]
    ordinals += [error.ordinal for error in parsed.parse_errors]
    return max(ordinals, default=None)


class Collector:
    """扫描会话目录并把新增内容落到 SQLite（设计文档 5.1 / 5.3 节）。"""

    def __init__(
        self,
        conn,
        *,
        sessions_dir,
        queue: ReportQueue | None = None,
        redactor: Redactor | None = None,
        granularity: str = "full",
        summary_chars: int = DEFAULT_SUMMARY_CHARS,
        patterns: tuple[str, ...] = ("*.jsonl",),
    ):
        self.conn = conn
        self.sessions_dir = Path(sessions_dir).expanduser()
        self.queue = queue
        self.redactor = redactor or Redactor()
        self.granularity = granularity
        self.summary_chars = summary_chars
        self.patterns = patterns

    def session_files(self) -> list[Path]:
        """按模式递归收集会话文件；目录不存在时返回空列表。"""
        if not self.sessions_dir.exists():
            return []
        found: list[Path] = []
        for pattern in self.patterns:
            found.extend(self.sessions_dir.rglob(pattern))
        return sorted(set(found))

    def scan_once(self) -> CollectOutcome:
        outcome = CollectOutcome(scans=1)
        for path in self.session_files():
            outcome.files_scanned += 1
            self._scan_file(path, outcome)
        return outcome

    def _scan_file(self, path: Path, outcome: CollectOutcome) -> None:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            return

        state = get_ingest_state(self.conn, key)
        if state is not None and state.file_size == stat.st_size and state.mtime == stat.st_mtime:
            return

        offset = state.byte_offset if state is not None else 0
        if state is not None and stat.st_size < offset:
            offset = 0
            outcome.files_truncated += 1

        increment = read_increment(path, offset)
        outcome.files_changed += 1
        outcome.lines_read += len(increment.lines)

        parsed = ParsedSession(
            session_id=(state.session_id if state and state.session_id else None)
            or session_id_from_filename(path),
            file_path=key,
        )
        for line_offset, raw in increment.lines:
            # 行自带 ordinal 优先；缺 ordinal 时用行首字节偏移做兜底幂等键，
            # 因为字节偏移在进程重启后依然稳定，进程内的行计数不稳定。
            merge_line_result(parsed, parse_line(raw, key, -1 - line_offset))

        for tool_result in parsed.tool_results:
            tool_result.result_summary = self.redactor.summarize(
                tool_result.output_text, limit=self.summary_chars
            )

        outcome.parse_errors += len(parsed.parse_errors)

        if increment.lines:
            result = write_parsed_session(self.conn, parsed)
            outcome.api_calls_written += result.inserted["api_calls"]
            outcome.tool_calls_written += result.inserted["tool_calls"]

            if self.queue is not None:
                envelopes: list[ReportEnvelope] = build_envelopes(
                    parsed,
                    redactor=self.redactor,
                    granularity=self.granularity,
                    summary_chars=self.summary_chars,
                )
                outcome.reports_queued += self.queue.enqueue_many(envelopes)

        previous_errors = state.parse_error_count if state is not None else 0
        update_ingest_state(
            self.conn,
            file_path=key,
            session_id=parsed.session_id,
            cli_version=parsed.cli_version,
            last_ordinal=_batch_max_ordinal(parsed),
            byte_offset=increment.byte_offset,
            file_size=stat.st_size,
            mtime=stat.st_mtime,
            parse_error_count=previous_errors + len(parsed.parse_errors),
        )

    def run(
        self,
        *,
        poll_interval: float = 2.0,
        stop_event: threading.Event | None = None,
        on_scan: Callable[[CollectOutcome], None] | None = None,
        max_scans: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> int:
        """周期扫描直到停止，返回实际扫描次数。

        用「轮询 + 增量」而不是事件监听（设计文档 5.3 节）：事件驱动在 Windows 与
        macOS 上行为不一致，也覆盖不到进程未运行期间产生的文件。
        sleep 与 stop_event 可注入，便于测试与优雅退出（设计文档 10 节）。
        """
        scans = 0
        while True:
            outcome = self.scan_once()
            scans += 1
            if on_scan is not None:
                on_scan(outcome)
            if max_scans is not None and scans >= max_scans:
                return scans
            if stop_event is not None and stop_event.is_set():
                return scans
            sleep(poll_interval)
