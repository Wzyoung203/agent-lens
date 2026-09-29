"""识别「加载 skill」的工具调用（设计文档 6.8）。

实测：skill 加载表现为 exec_command / spawn_agent 的参数里出现
`.../skills/<name>/SKILL.md` 路径。本模块只做纯识别，不读文件、不写库。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SKILL_PATH_RE = re.compile(r"(?P<path>[\w./~+-]*/skills?/(?P<name>[\w.+-]+)/SKILL\.md)")


@dataclass(frozen=True)
class SkillHit:
    file_path: str
    ordinal: int
    skill_name: str
    skill_path: str
    tool_name: str


def _arguments(payload: dict) -> str:
    parts: list[str] = []
    for key in ("arguments", "input", "parameters"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, ensure_ascii=False))
    return "\n".join(parts)


def find_skill_hits(lines: Iterable[str], *, file_path: str) -> list[SkillHit]:
    hits: list[SkillHit] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("type") != "response_item":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") not in ("function_call", "custom_tool_call"):
            continue
        ordinal = row.get("ordinal")
        if not isinstance(ordinal, int):
            continue
        text = _arguments(payload)
        seen: set[str] = set()
        for match in SKILL_PATH_RE.finditer(text):
            name = match.group("name")
            if name in seen:
                continue
            seen.add(name)
            hits.append(
                SkillHit(
                    file_path=file_path,
                    ordinal=ordinal,
                    skill_name=name,
                    skill_path=match.group("path"),
                    tool_name=str(payload.get("name") or ""),
                )
            )
    return hits


def find_skill_hits_in_file(path: Path) -> list[SkillHit]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return find_skill_hits(handle, file_path=str(path))
