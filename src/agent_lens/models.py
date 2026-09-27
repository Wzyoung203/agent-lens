"""Codex 会话日志的结构化模型。

全部结论依据对 8 个真实会话文件、321 条 token 记录的实测，
详见 docs/superpowers/specs/2026-09-24-agent-lens-design.md 第 4 节。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def to_utc(timestamp: str) -> datetime:
    """把 ISO 8601 字符串归一化为 UTC aware datetime。"""
    return datetime.fromisoformat(timestamp).astimezone(UTC)


def epoch_to_utc(value: float | None) -> datetime | None:
    """把 Unix 秒级时间戳归一化为 UTC aware datetime。

    codex 的 task_started / task_complete 用秒级 epoch 记录轮次起止时间；
    缺字段、类型不对或数值不可解析时返回 None，不猜。
    """
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


class TokenUsage(BaseModel):
    """单次 API 调用的 token 用量。

    实测包含关系（321 条记录零例外）：
      cached_input_tokens <= input_tokens
      reasoning_output_tokens <= output_tokens

    因 output_tokens 已包含 reasoning_output_tokens，计费时两者不可相加。
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @model_validator(mode="after")
    def _validate_containment(self) -> TokenUsage:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError(
                f"cached_input_tokens({self.cached_input_tokens}) 不能大于 "
                f"input_tokens({self.input_tokens})"
            )
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError(
                f"reasoning_output_tokens({self.reasoning_output_tokens}) 不能大于 "
                f"output_tokens({self.output_tokens})"
            )
        return self

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率。input 为 0 时返回 0.0，避免除零。"""
        if self.input_tokens == 0:
            return 0.0
        return self.cached_input_tokens / self.input_tokens

    @property
    def uncached_input_tokens(self) -> int:
        """未命中缓存的 input，即按高价计费的部分。"""
        return self.input_tokens - self.cached_input_tokens


ToolKind = Literal["function_call", "custom_tool_call", "web_search_call", "tool_search_call"]


class SessionMetaRecord(BaseModel):
    """session_meta 行。"""

    session_id: str
    cwd: str | None = None
    cli_version: str | None = None
    model_provider: str | None = None
    recorded_at: datetime | None = None
    base_instructions_chars: int = 0


class TurnContextRecord(BaseModel):
    """turn_context 行，逐轮记录模型与推理强度。"""

    turn_id: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: str | None = None
    collaboration_mode: str | None = None


class ApiCallRecord(BaseModel):
    """token_usage_record：一次 LLM API 调用。

    thread_input_tokens_cumulative 只用于自检。实测该字段在每个文件内会重置，
    因此任何总量统计都必须对 usage 求和，不可读取累计字段（设计文档 4.2 节）。
    """

    file_path: str
    ordinal: int
    session_id: str | None = None
    turn_id: str | None = None
    response_id: str | None = None
    timestamp: datetime | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    turn_input_tokens_cumulative: int | None = None
    thread_input_tokens_cumulative: int | None = None

    @property
    def idempotency_key(self) -> tuple[str, int]:
        return (self.file_path, self.ordinal)


class ToolCallRecord(BaseModel):
    """工具调用请求。"""

    file_path: str
    ordinal: int
    call_id: str | None = None
    name: str
    kind: ToolKind = "function_call"
    arguments_raw: str = ""


class ToolResultRecord(BaseModel):
    """工具调用结果。"""

    file_path: str
    ordinal: int
    call_id: str | None = None
    output_text: str = ""
    exit_code: int | None = None
    wall_time_seconds: float | None = None
    success: bool | None = None


class ItemCompletedRecord(BaseModel):
    """item_completed 事件，带毫秒级起止时间。"""

    file_path: str
    ordinal: int
    item_type: str
    started_at_ms: int
    completed_at_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.completed_at_ms - self.started_at_ms)


class TurnRecord(BaseModel):
    """轮次的起止与结束原因。"""

    turn_id: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    aborted_reason: str | None = None


class ParseError(BaseModel):
    """单行解析失败的记录。"""

    file_path: str
    ordinal: int
    reason: str
    raw_preview: str


class ParsedSession(BaseModel):
    """一个会话文件的完整解析结果。

    一个 Codex 会话可能横跨多个文件（设计文档 4.3 节），因此本对象对应的是
    「一个文件」，跨文件的会话合并由存储层负责。
    """

    session_id: str
    file_path: str
    cli_version: str | None = None
    cwd: str | None = None
    model_provider: str | None = None
    base_instructions_chars: int = 0
    recorded_at: datetime | None = None
    turns: list[TurnRecord] = Field(default_factory=list)
    turn_contexts: list[TurnContextRecord] = Field(default_factory=list)
    api_calls: list[ApiCallRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tool_results: list[ToolResultRecord] = Field(default_factory=list)
    items: list[ItemCompletedRecord] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    parse_errors: list[ParseError] = Field(default_factory=list)
    partial_tail: str | None = None

    @property
    def total_input_tokens(self) -> int:
        """总量一律对单次 usage 求和，绝不读取累计字段（设计文档 4.2 节）。"""
        return sum(call.usage.input_tokens for call in self.api_calls)

    @property
    def last_thread_input_tokens(self) -> int | None:
        """文件内最后一条非空累计值，只用于自检。"""
        for call in reversed(self.api_calls):
            if call.thread_input_tokens_cumulative is not None:
                return call.thread_input_tokens_cumulative
        return None


class VerificationResult(BaseModel):
    """累计字段自检结果，用于回归测试与数据质量告警。"""

    file_path: str
    session_id: str
    summed_input_tokens: int
    last_thread_input_tokens: int | None
    matches: bool
