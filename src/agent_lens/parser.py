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
