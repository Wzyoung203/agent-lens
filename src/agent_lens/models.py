"""Codex 会话日志的结构化模型。

全部结论依据对 8 个真实会话文件、321 条 token 记录的实测，
详见 docs/superpowers/specs/2026-09-24-agent-lens-design.md 第 4 节。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, model_validator


def to_utc(timestamp: str) -> datetime:
    """把 ISO 8601 字符串归一化为 UTC aware datetime。"""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)


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
