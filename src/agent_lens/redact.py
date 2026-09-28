"""写库摘要与上报字段的脱敏（设计文档第 8 节）。

两条硬性约定：
  1. 原始 JSONL 永不修改——脱敏只作用于「写进 SQLite 的摘要」与「发往 Langfuse 的字段」。
  2. 宁可多报不可漏报：命中即整体替换为 [REDACTED:<类型>]，不留任何原文字符。

本项目开发期间真实发生过一次凭据泄露（检查 ~/.git-credentials 时把 token 打进了会话，
而会话又被写进 ~/.codex/sessions）。所以这是写库前的强制步骤，不是可选功能。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

REDACTED_TEMPLATE = "[REDACTED:{name}]"
DEFAULT_SUMMARY_CHARS = 200

# 顺序有意义：先匹配结构最明确的形态（PEM / JWT / 带前缀的 token），
# 再兜底泛化的 key=value 赋值。
# 每组是 (类型名, 正则, 只替换的捕获组序号)；组号为 None 表示替换整个匹配。
_BUILTIN_RULES: tuple[tuple[str, str, int | None], ...] = (
    (
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        None,
    ),
    ("github_pat", r"github_pat_[A-Za-z0-9_]{20,}", None),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{20,}", None),
    ("openai_key", r"sk-[A-Za-z0-9_\-]{16,}", None),
    ("aws_access_key", r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])", None),
    ("slack_token", r"xox[baprs]-[A-Za-z0-9-]{10,}", None),
    ("jwt", r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", None),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{16,}=*", None),
    (
        "assignment",
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|auth[_-]?token|credentials)\b"
        r"\s*[:=]\s*[\"']?([^\s\"',;]{6,})",
        1,
    ),
)


class RedactionRule(BaseModel):
    """一条脱敏规则。group 非空时只替换该捕获组，从而保留 key= 前缀。"""

    name: str
    pattern: str
    group: int | None = None
    enabled: bool = True


class RedactionHit(BaseModel):
    """某个规则在一次文本里命中的次数。刻意不保留原文，避免脱敏日志本身泄露。"""

    rule: str
    count: int


class RedactionResult(BaseModel):
    text: str
    hits: list[RedactionHit] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(hit.count for hit in self.hits)


class Redactor:
    """无状态、可复用的脱敏器。默认规则覆盖设计文档第 8 节的清单。"""

    def __init__(self, extra_rules: list[RedactionRule] | None = None, *, enabled: bool = True):
        self.enabled = enabled
        rules = [
            RedactionRule(name=name, pattern=pattern, group=group)
            for name, pattern, group in _BUILTIN_RULES
        ]
        rules.extend(extra_rules or [])
        self._rules = [rule for rule in rules if rule.enabled]
        self._compiled = [(rule, re.compile(rule.pattern)) for rule in self._rules]

    def redact(self, text: str) -> RedactionResult:
        """替换全部命中并统计。enabled=False 时原样返回。"""
        if not self.enabled or not text:
            return RedactionResult(text=text)
        counts: dict[str, int] = {}
        result = text
        for rule, pattern in self._compiled:
            token = REDACTED_TEMPLATE.format(name=rule.name)

            def _replace(match: re.Match[str], _rule=rule, _token=token) -> str:
                counts[_rule.name] = counts.get(_rule.name, 0) + 1
                if _rule.group is None:
                    return _token
                start, end = match.span(_rule.group)
                whole = match.group(0)
                rel_start = start - match.start()
                rel_end = end - match.start()
                return whole[:rel_start] + _token + whole[rel_end:]

            result = pattern.sub(_replace, result)
        hits = [RedactionHit(rule=name, count=counts[name]) for name in counts]
        return RedactionResult(text=result, hits=hits)

    def redact_text(self, text: str) -> str:
        return self.redact(text).text

    def dry_run(self, text: str) -> list[RedactionHit]:
        """只看命中情况，不关心替换结果。用于配置调参与人工核对。"""
        return self.redact(text).hits

    def summarize(self, text: str, *, limit: int = DEFAULT_SUMMARY_CHARS) -> str:
        """把正文压成单行摘要，作为入库与上报的正文替身。

        顺序是「先脱敏、再压平、最后截断」：如果先截断，落在截断点之外的密钥
        会因为正则拿不到完整匹配而漏网；先脱敏就没有这个问题。
        """
        if not text:
            return ""
        redacted = self.redact_text(text)
        flat = " ".join(redacted.split())
        if len(flat) <= limit:
            return flat
        cut = flat[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut + " …[截断]"
