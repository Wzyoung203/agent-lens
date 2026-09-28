"""增量采集：扫描会话目录 -> 读新增字节 -> 解析 -> 脱敏 -> 写库 -> 入上报队列。

职责边界（设计文档 5.1 节）：
  * Collector 只读 ~/.codex，绝不修改或删除原始文件；
  * 采集与上报解耦：采集器只往 report_queue 写待发记录，发送是 reporter.py 的事；
  * 幂等键 (file_path, ordinal) 来自日志行自带的 ordinal，重复扫描不产生重复行。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


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
