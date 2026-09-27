"""从真实 Codex 会话日志生成脱敏的测试 fixture。

用法：
    uv run python scripts/make_fixture.py <真实会话文件> tests/fixtures/real_session_sample.jsonl

处理规则只改正文，不动任何数值字段，因此 token 总量保持不变：
  - base_instructions.text 替换为等长的 x 填充（长度保留，内容不保留）
  - message / reasoning 的正文替换为 [SAMPLE]
  - function_call 的 arguments 截断到 120 字符
  - 工具输出的 output 只保留元数据行（Chunk ID / Wall time / Exit code / Output）
  - 密钥形态替换为 [REDACTED]

脱敏采用**白名单**：只保留解析器真正需要的字段，其余嵌套正文（工具输出的 stdout /
aggregated_output / formatted_output、item.changes 里的文件内容、item.command、
last_agent_message、developer_instructions、host_skills.body 等）一律丢弃。
原因：只替换 arguments / output / message 的做法实测在真实日志上会漏掉本机
.zshenv / .zshrc 的完整内容、shell 命令与 agent 回复原文。

脚本末尾有一道自检：生成结果里若仍出现用户绝对路径、邮箱或密钥形态就直接失败，
不会写出带敏感内容的 fixture。
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

USER_PATH_RE = re.compile(r"/Users/(?!\[USER\])[^/\s\"']+")
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\\\[^\s\"']+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

ARGUMENTS_LIMIT = 120
SAMPLE = "[SAMPLE]"
USER_PLACEHOLDER = "[USER]"
TOOL_CALL_TYPES = ("function_call", "custom_tool_call")
TOOL_RESULT_TYPES = ("function_call_output", "custom_tool_call_output")
USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def redact_text(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def fake_path(value: object) -> str:
    """把用户主目录与盘符路径替换成占位符。"""
    if not isinstance(value, str):
        return ""
    value = USER_PATH_RE.sub(USER_PLACEHOLDER, value)
    value = WINDOWS_PATH_RE.sub("[WORKSPACE]", value)
    return redact_text(value)


def as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def keep_metadata_only(text: str) -> str:
    return "\n".join(METADATA_LINE_RE.findall(text)) + "\n"


def pick_usage(value: object) -> dict[str, int] | None:
    """只保留六项 token 数值字段，其余键丢弃。"""
    if not isinstance(value, dict):
        return None
    return {key: int(value[key]) for key in USAGE_KEYS if isinstance(value.get(key), int)}


def reduced_mode(value: object) -> dict | None:
    """collaboration_mode 只保留 mode，丢弃 developer_instructions 等自由文本。"""
    if not isinstance(value, dict):
        return None
    return {"mode": value.get("mode")} if value.get("mode") is not None else None


def sanitize_payload(row_type: object, payload: dict) -> dict:
    kind = payload.get("type")

    if row_type == "session_meta":
        base = payload.get("base_instructions")
        base_text = base.get("text") if isinstance(base, dict) else None
        return {
            "session_id": payload.get("session_id"),
            "timestamp": payload.get("timestamp"),
            "cwd": fake_path(payload.get("cwd")),
            "cli_version": payload.get("cli_version"),
            "model_provider": payload.get("model_provider"),
            "base_instructions": {
                "text": "x" * len(base_text) if isinstance(base_text, str) else ""
            },
        }

    if row_type == "turn_context":
        return {
            "turn_id": payload.get("turn_id"),
            "cwd": fake_path(payload.get("cwd")),
            "model": payload.get("model"),
            "effort": payload.get("effort"),
            "collaboration_mode": reduced_mode(payload.get("collaboration_mode")),
        }

    if row_type == "token_usage_record":
        return {
            "thread_id": payload.get("thread_id"),
            "turn_id": payload.get("turn_id"),
            "session_id": payload.get("session_id"),
            "response_id": payload.get("response_id"),
            "usage": pick_usage(payload.get("usage")),
            "turn_token_usage": pick_usage(payload.get("turn_token_usage")),
            "thread_token_usage": pick_usage(payload.get("thread_token_usage")),
        }

    if row_type == "response_item":
        if kind == "message":
            return {"type": "message", "role": payload.get("role"), "content": [SAMPLE]}
        if kind == "reasoning":
            return {"type": "reasoning", "summary": [SAMPLE], "content": [SAMPLE]}
        if kind in TOOL_CALL_TYPES:
            raw = payload.get("arguments")
            if raw is None:
                raw = payload.get("input")
            key = "arguments" if kind == "function_call" else "input"
            return {
                "type": kind,
                "name": payload.get("name"),
                "call_id": payload.get("call_id"),
                key: fake_path(as_text(raw)[:ARGUMENTS_LIMIT]),
            }
        if kind in TOOL_RESULT_TYPES:
            text = as_text(payload.get("output"))
            return {
                "type": kind,
                "call_id": payload.get("call_id"),
                "output": fake_path(keep_metadata_only(text)),
            }
        return {"type": kind}

    if row_type == "event_msg":
        if kind == "item_completed":
            item = payload.get("item")
            item = item if isinstance(item, dict) else {}
            return {
                "type": "item_completed",
                "item": {"type": item.get("type")},
                "started_at_ms": payload.get("started_at_ms"),
                "completed_at_ms": payload.get("completed_at_ms"),
            }
        if kind == "task_complete":
            return {
                "type": "task_complete",
                "turn_id": payload.get("turn_id"),
                "started_at": payload.get("started_at"),
                "completed_at": payload.get("completed_at"),
                "duration_ms": payload.get("duration_ms"),
                "time_to_first_token_ms": payload.get("time_to_first_token_ms"),
            }
        if kind == "task_started":
            return {
                "type": "task_started",
                "turn_id": payload.get("turn_id"),
                "started_at": payload.get("started_at"),
                "model_context_window": payload.get("model_context_window"),
            }
        if kind == "turn_aborted":
            return {
                "type": "turn_aborted",
                "turn_id": payload.get("turn_id"),
                "duration_ms": payload.get("duration_ms"),
                "reason": redact_text(as_text(payload.get("reason"))),
            }
        if kind == "token_count":
            info = payload.get("info")
            info = info if isinstance(info, dict) else {}
            return {
                "type": "token_count",
                "info": {
                    "total_token_usage": pick_usage(info.get("total_token_usage")),
                    "last_token_usage": pick_usage(info.get("last_token_usage")),
                    "model_context_window": info.get("model_context_window"),
                },
            }
        if kind == "thread_settings_applied":
            settings = payload.get("thread_settings")
            settings = settings if isinstance(settings, dict) else {}
            return {
                "type": "thread_settings_applied",
                "thread_id": payload.get("thread_id"),
                "thread_settings": {
                    "model": settings.get("model"),
                    "cwd": fake_path(settings.get("cwd")),
                    "reasoning_effort": settings.get("reasoning_effort"),
                    "collaboration_mode": reduced_mode(settings.get("collaboration_mode")),
                },
            }
        return {"type": kind}

    if row_type == "world_state":
        state = payload.get("state")
        state = state if isinstance(state, dict) else {}
        return {
            "full": payload.get("full"),
            "state": {
                "model": state.get("model"),
                "timezone": state.get("timezone"),
                "collaboration_mode": reduced_mode(state.get("collaboration_mode")),
            },
        }

    return {}


def sanitize_row(raw: str) -> str:
    row = json.loads(raw)
    payload = row.get("payload")
    sanitized = {
        "ordinal": row.get("ordinal"),
        "type": row.get("type"),
        "payload": sanitize_payload(row.get("type"), payload) if isinstance(payload, dict) else {},
    }
    return redact_text(json.dumps(sanitized, ensure_ascii=False))


def assert_clean(text: str) -> None:
    """生成结果自检：仍含用户路径、邮箱或密钥形态则中止。"""
    offenders = {
        "user_path": len(USER_PATH_RE.findall(text)),
        "windows_path": len(WINDOWS_PATH_RE.findall(text)),
        "email": len(EMAIL_RE.findall(text)),
        **{f"secret:{i}": len(p.findall(text)) for i, p in enumerate(SECRET_PATTERNS)},
    }
    dirty = {name: count for name, count in offenders.items() if count}
    if dirty:
        raise SystemExit(f"fixture 自检未通过，仍有可疑内容：{dirty}")


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
        out_lines.append(sanitize_row(raw))

    body = "\n".join(out_lines) + "\n"
    assert_clean(body)
    target_path.write_text(body, encoding="utf-8")

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
