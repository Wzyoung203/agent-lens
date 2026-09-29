"""聚合查询层：只读 SQLite，返回 pydantic 模型。

设计约定（设计文档 6.4 / 6.5 / 6.6 / 4.11 节）：
  * 成本不落库，全部在查询时按价目表算，所以改价后历史趋势一致重算；
  * 时间分桶一律用 `api_call_view.occurred_at`——实测 `token_usage_record` 不带时间戳，
    `api_calls.timestamp` 在真实数据上恒为 NULL，`occurred_at` 回落到 turn 起始时间；
  * `total_tokens = input + output`；`reasoning_output_tokens` 已含在 output 内，不重复相加；
  * 轮次归属：`tool_calls` 行本身不带 turn_id，按「同一文件里其后第一条 api_call 的轮次」
    近似归属（与 reporter.build_envelopes 用的是同一条实测规则）。
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from .context import BLOCK_UNATTRIBUTED
from .context import BLOCKS as BLOCK_ORDER
from .pricing import Pricer
from .storage import to_iso


class RangeInfo(BaseModel):
    start: str
    end: str
    days: int
    timezone: str = "UTC"


class MetricCards(BaseModel):
    total_cost: float = 0.0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_hit_rate: float = 0.0
    session_count: int = 0
    turn_count: int = 0
    api_call_count: int = 0
    tool_call_count: int = 0
    tool_failure_rate: float = 0.0
    unpriced_calls: int = 0
    currency: str = ""


class DailyPoint(BaseModel):
    day: str
    cost: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0
    cache_hit_rate: float = 0.0


class ProjectStat(BaseModel):
    project: str
    cost: float = 0.0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_rate: float = 0.0
    session_count: int = 0
    api_call_count: int = 0
    tool_failure_rate: float = 0.0


class CostBreakdown(BaseModel):
    uncached_input: float = 0.0
    cached_input: float = 0.0
    output: float = 0.0
    total: float = 0.0
    currency: str = ""


class OverviewResponse(BaseModel):
    range: RangeInfo
    cards: MetricCards
    daily: list[DailyPoint] = Field(default_factory=list)
    projects: list[ProjectStat] = Field(default_factory=list)


class SessionSummary(BaseModel):
    session_id: str
    project: str
    cwd: str | None = None
    cli_version: str | None = None
    model_provider: str | None = None
    first_seen_at: str | None = None
    updated_at: str | None = None
    recorded_at: str | None = None
    turn_count: int = 0
    api_call_count: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    cache_hit_rate: float = 0.0
    first_api_at: str | None = None
    last_api_at: str | None = None


class TurnSummary(BaseModel):
    turn_id: str
    index: int
    model: str | None = None
    effort: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    aborted_reason: str | None = None
    api_call_count: int = 0
    tool_call_count: int = 0
    tool_failure_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_rate: float = 0.0
    cost: float = 0.0
    context_growth: float | None = None


class ApiCallRow(BaseModel):
    ordinal: int
    file_path: str
    response_id: str | None = None
    timestamp: str | None = None
    model: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_rate: float = 0.0
    cost: float = 0.0
    priced: bool = False


class ToolCallRow(BaseModel):
    ordinal: int
    call_id: str | None = None
    name: str = ""
    kind: str = "function_call"
    arguments_chars: int = 0
    output_chars: int = 0
    exit_code: int | None = None
    wall_time_seconds: float | None = None
    success: bool | None = None
    duration_ms: int | None = None
    result_summary: str | None = None


class SessionDetail(BaseModel):
    session: SessionSummary
    turns: list[TurnSummary] = Field(default_factory=list)
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)


class TurnDetail(BaseModel):
    turn: TurnSummary
    api_calls: list[ApiCallRow] = Field(default_factory=list)
    tool_calls: list[ToolCallRow] = Field(default_factory=list)


class ProjectDetail(BaseModel):
    range: RangeInfo
    project: ProjectStat
    daily: list[DailyPoint] = Field(default_factory=list)
    sessions: list[SessionSummary] = Field(default_factory=list)
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)


class ToolStat(BaseModel):
    name: str
    call_count: int = 0
    failure_count: int = 0
    failure_rate: float = 0.0
    avg_duration_ms: float | None = None
    p95_duration_ms: float | None = None
    total_output_chars: int = 0


class ToolFailure(BaseModel):
    session_id: str
    turn_id: str | None = None
    project: str
    call_id: str | None = None
    name: str = ""
    exit_code: int | None = None
    wall_time_seconds: float | None = None
    result_summary: str | None = None
    ordinal: int = 0
    file_path: str = ""


class ProjectToolFailure(BaseModel):
    project: str
    call_count: int = 0
    failure_count: int = 0
    failure_rate: float = 0.0


class ToolsResponse(BaseModel):
    range: RangeInfo
    stats: list[ToolStat] = Field(default_factory=list)
    failures: list[ToolFailure] = Field(default_factory=list)
    failure_cost: CostBreakdown = Field(default_factory=CostBreakdown)
    by_project: list[ProjectToolFailure] = Field(default_factory=list)


class Page[T](BaseModel):
    items: list[T] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class ContextBlockStat(BaseModel):
    """上下文分解里的一块（前四块有字符数，unattributed 是残差）。"""

    block: str
    tokens: float
    share: float
    cjk_chars: int = 0
    other_chars: int = 0
    estimated_tokens: float = 0.0


class ContextTrendPoint(BaseModel):
    day: str
    block: str
    tokens: float


class ContextOverviewResponse(BaseModel):
    range: RangeInfo
    blocks: list[ContextBlockStat]
    trend: list[ContextTrendPoint] = Field(default_factory=list)
    input_tokens: int
    analyzed_calls: int
    total_calls: int
    coverage: float
    cache_hit_rate: float


def context_overview(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    now: datetime | None = None,
) -> ContextOverviewResponse:
    """上下文构成：按块聚合，并曝光「有多少调用还没分析」的覆盖率。

    分解行自身没有时间列，所以先用 `api_call_view` 取时间窗内的白名单
    （`(file_path, ordinal)`），再用白名单过滤分解行——这样时间范围与项目过滤
    都与其它页面口径一致。
    """
    start, end = _range_bounds(now, days)
    api_rows = _fetch_api_rows(conn, start, end, project=project)
    total_calls = len(api_rows)
    allowed = {(row["file_path"], row["ordinal"]) for row in api_rows}
    input_tokens = sum(_row_tokens(row, "input_tokens") for row in api_rows)
    cached = sum(_row_tokens(row, "cached_input_tokens") for row in api_rows)

    totals: dict[str, float] = {}
    chars: dict[str, list[int]] = {}
    estimates: dict[str, float] = {}
    analyzed = 0
    if allowed:
        rows = conn.execute(
            """
            SELECT file_path, ordinal, block, cjk_chars, other_chars,
                   estimated_tokens, attributed_tokens
            FROM context_breakdown
            """
        ).fetchall()
        seen: set[tuple[str, int]] = set()
        for row in rows:
            key = (row["file_path"], row["ordinal"])
            if key not in allowed:
                continue
            seen.add(key)
            block = row["block"]
            totals[block] = totals.get(block, 0.0) + (row["attributed_tokens"] or 0.0)
            slot = chars.setdefault(block, [0, 0])
            slot[0] += row["cjk_chars"] or 0
            slot[1] += row["other_chars"] or 0
            estimates[block] = estimates.get(block, 0.0) + (row["estimated_tokens"] or 0.0)
        analyzed = len(seen)

    attributed_total = sum(totals.values())
    blocks = [
        ContextBlockStat(
            block=name,
            tokens=totals.get(name, 0.0),
            share=(totals.get(name, 0.0) / attributed_total) if attributed_total else 0.0,
            cjk_chars=chars.get(name, [0, 0])[0],
            other_chars=chars.get(name, [0, 0])[1],
            estimated_tokens=estimates.get(name, 0.0),
        )
        for name in (*BLOCK_ORDER, BLOCK_UNATTRIBUTED)
    ]
    return ContextOverviewResponse(
        range=_range_info(start, end, days),
        blocks=blocks,
        trend=[],
        input_tokens=input_tokens,
        analyzed_calls=analyzed,
        total_calls=total_calls,
        coverage=(analyzed / total_calls) if total_calls else 0.0,
        cache_hit_rate=_cache_hit_rate(cached, input_tokens),
    )


class SkillStat(BaseModel):
    skill_name: str
    loads: int
    session_count: int
    tool_names: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None


class SkillsResponse(BaseModel):
    range: RangeInfo
    skills: list[SkillStat] = Field(default_factory=list)
    total_loads: int = 0


def skill_stats(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    now: datetime | None = None,
) -> SkillsResponse:
    """skill 命中排行。

    `skill_hits` 故意不存 session_id / turn_id（工具调用行本身没有这两个字段，
    猜一个会污染归属），这里用 `(file_path, ordinal)` 关联 `tool_calls` 拿回来。
    """
    start, end = _range_bounds(now, days)
    sql = """
        SELECT h.skill_name AS skill_name,
               h.tool_name  AS tool_name,
               c.session_id AS session_id,
               COALESCE(t.started_at, s.recorded_at, s.first_seen_at) AS occurred_at
        FROM skill_hits h
        LEFT JOIN tool_calls c ON c.file_path = h.file_path AND c.ordinal = h.ordinal
        LEFT JOIN turns t ON t.session_id = c.session_id AND t.turn_id = c.turn_id
        LEFT JOIN sessions s ON s.session_id = c.session_id
        WHERE COALESCE(t.started_at, s.recorded_at, s.first_seen_at) >= ?
          AND COALESCE(t.started_at, s.recorded_at, s.first_seen_at) < ?
    """
    params: list[object] = [to_iso(start), to_iso(end)]
    if project:
        sql += " AND s.project = ?"
        params.append(project)

    buckets: dict[str, dict[str, object]] = {}
    for row in conn.execute(sql, params).fetchall():
        bucket = buckets.setdefault(
            row["skill_name"],
            {"loads": 0, "sessions": set(), "tools": set(), "seen": []},
        )
        bucket["loads"] += 1
        if row["session_id"]:
            bucket["sessions"].add(row["session_id"])
        if row["tool_name"]:
            bucket["tools"].add(row["tool_name"])
        if row["occurred_at"]:
            bucket["seen"].append(row["occurred_at"])

    stats: list[SkillStat] = []
    for name, bucket in buckets.items():
        seen = sorted(bucket["seen"])
        stats.append(
            SkillStat(
                skill_name=name,
                loads=int(bucket["loads"]),
                session_count=len(bucket["sessions"]),
                tool_names=sorted(bucket["tools"]),
                first_seen=seen[0] if seen else None,
                last_seen=seen[-1] if seen else None,
            )
        )
    stats.sort(key=lambda item: (-item.loads, item.skill_name))
    return SkillsResponse(
        range=_range_info(start, end, days),
        skills=stats,
        total_loads=sum(item.loads for item in stats),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _range_bounds(now: datetime | None, days: int) -> tuple[datetime, datetime]:
    """窗口是「含当天在内、向前推 days 天」，两端都取当天 00:00 UTC。"""
    moment = (now or _utc_now()).astimezone(UTC)
    today = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=1)
    start = end - timedelta(days=max(0, days))
    return start, end


def _range_info(start: datetime, end: datetime, days: int) -> RangeInfo:
    return RangeInfo(start=to_iso(start), end=to_iso(end), days=days)


def _fetch_api_rows(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    *,
    project: str | None = None,
    provider: str | None = None,
    session_id: str | None = None,
) -> list[sqlite3.Row]:
    """取窗口内的 api_call 行。

    用 occurred_at 而不是 timestamp：真实数据上 timestamp 恒为 NULL（设计文档 4.11）。
    """
    sql = "SELECT * FROM api_call_view WHERE occurred_at >= ? AND occurred_at < ?"
    params: list[object] = [to_iso(start), to_iso(end)]
    if project:
        sql += " AND project = ?"
        params.append(project)
    if provider:
        sql += " AND model_provider = ?"
        params.append(provider)
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    sql += " ORDER BY occurred_at, ordinal"
    return conn.execute(sql, params).fetchall()


def _row_tokens(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    return int(value) if value is not None else 0


def _cache_hit_rate(cached: int, total_input: int) -> float:
    return cached / total_input if total_input else 0.0


class _CostedRows:
    """一次查询内共用一个 Pricer 与它的查价缓存。"""

    def __init__(self, conn: sqlite3.Connection, *, now: datetime | None = None):
        self._pricer = Pricer(conn, now=now)

    def price_for(self, row: sqlite3.Row):
        """按这一行自己的发生时刻选价（时段由 occurred_at 决定，不是由 now 决定）。"""
        return self._pricer.price_for(
            row["model_provider"], row["model"], _row_moment(row)
        )

    def cost(self, row: sqlite3.Row) -> tuple[float, bool]:
        value = self._pricer.cost_for(row)
        if value is None:
            return 0.0, False
        return value, True

    def currency(self, row: sqlite3.Row) -> str:
        price = self.price_for(row)
        return price.currency if price else ""


def _row_moment(row: sqlite3.Row) -> datetime | None:
    """行内可用的最佳时间锚点：调用级 timestamp 优先，否则用 occurred_at（4.11 节）。"""
    keys = row.keys()
    for key in ("timestamp", "occurred_at"):
        if key in keys and row[key]:
            return datetime.fromisoformat(str(row[key]))
    return None


def _breakdown(rows: list[sqlite3.Row], costed: _CostedRows) -> CostBreakdown:
    breakdown = CostBreakdown()
    for row in rows:
        price = costed.price_for(row)
        value, priced = costed.cost(row)
        if not priced:
            continue
        if price is not None:
            scale = 1_000_000
            breakdown.uncached_input += (
                _row_tokens(row, "input_tokens") - _row_tokens(row, "cached_input_tokens")
            ) * price.input_price_per_mtok / scale
            breakdown.cached_input += (
                _row_tokens(row, "cached_input_tokens") * price.cached_input_price_per_mtok / scale
            )
            breakdown.output += (
                _row_tokens(row, "output_tokens") * price.output_price_per_mtok / scale
            )
            breakdown.currency = price.currency
        breakdown.total += value
    return breakdown


def _aggregate(rows: list[sqlite3.Row], costed: _CostedRows) -> MetricCards:
    cards = MetricCards()
    for row in rows:
        value, priced = costed.cost(row)
        cards.total_cost += value
        if not priced and (
            _row_tokens(row, "input_tokens") or _row_tokens(row, "output_tokens")
        ):
            cards.unpriced_calls += 1
        cards.input_tokens += _row_tokens(row, "input_tokens")
        cards.cached_input_tokens += _row_tokens(row, "cached_input_tokens")
        cards.output_tokens += _row_tokens(row, "output_tokens")
        cards.api_call_count += 1
        currency = costed.currency(row)
        if currency:
            cards.currency = currency
    cards.total_tokens = cards.input_tokens + cards.output_tokens
    cards.cache_hit_rate = _cache_hit_rate(cards.cached_input_tokens, cards.input_tokens)
    return cards


def _daily(rows: list[sqlite3.Row], costed: _CostedRows) -> list[DailyPoint]:
    buckets: dict[str, DailyPoint] = {}
    for row in rows:
        occurred = row["occurred_at"]
        if not occurred:
            continue
        day = str(occurred)[:10]
        point = buckets.setdefault(day, DailyPoint(day=day))
        value, _ = costed.cost(row)
        point.cost += value
        point.input_tokens += _row_tokens(row, "input_tokens")
        point.cached_input_tokens += _row_tokens(row, "cached_input_tokens")
        point.output_tokens += _row_tokens(row, "output_tokens")
        point.api_calls += 1
    for point in buckets.values():
        point.cache_hit_rate = _cache_hit_rate(point.cached_input_tokens, point.input_tokens)
    return [buckets[day] for day in sorted(buckets)]


def _tool_rows(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    *,
    project: str | None = None,
    session_id: str | None = None,
) -> list[sqlite3.Row]:
    sql = """
        SELECT d.*, s.project AS project, s.cwd AS cwd
        FROM tool_call_details d
        LEFT JOIN sessions s ON s.session_id = d.session_id
        WHERE 1 = 1
    """
    params: list[object] = []
    if project:
        sql += " AND s.project = ?"
        params.append(project)
    if session_id:
        sql += " AND d.session_id = ?"
        params.append(session_id)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return rows
    # 工具调用行没有自己的时间戳，用「同一文件里最近一条 api_call 的 occurred_at」定位。
    anchors = {
        (row["file_path"], row["ordinal"]): row["occurred_at"]
        for row in conn.execute(
            "SELECT file_path, ordinal, occurred_at FROM api_call_view"
        ).fetchall()
    }
    kept: list[sqlite3.Row] = []
    for row in rows:
        anchor = _anchor_time(anchors, row["file_path"], row["call_ordinal"])
        if anchor is None:
            continue
        moment = datetime.fromisoformat(anchor)
        if start <= moment < end:
            kept.append(row)
    return kept


def _anchor_time(
    anchors: dict[tuple[str, int], str | None], file_path: str, ordinal: int
) -> str | None:
    candidates = sorted(
        (item_ordinal, value)
        for (item_path, item_ordinal), value in anchors.items()
        if item_path == file_path and value
    )
    if not candidates:
        return None
    ordinals = [item[0] for item in candidates]
    index = bisect_right(ordinals, ordinal)
    if index >= len(ordinals):
        index = len(ordinals) - 1
    return candidates[index][1]


def _is_failure(row: sqlite3.Row) -> bool:
    if row["success"] is None:
        return False
    return not bool(row["success"])


def _duration_ms(row: sqlite3.Row) -> int | None:
    """工具耗时取输出里的 Wall time；item_completed 无法与 call_id 关联，故不用。"""
    wall = row["wall_time_seconds"]
    if wall is None:
        return None
    return round(float(wall) * 1000)


def overview(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    provider: str | None = None,
    now: datetime | None = None,
) -> OverviewResponse:
    start, end = _range_bounds(now, days)
    costed = _CostedRows(conn, now=now)
    rows = _fetch_api_rows(conn, start, end, project=project, provider=provider)
    cards = _aggregate(rows, costed)
    cards.session_count = len({row["session_id"] for row in rows if row["session_id"]})
    cards.turn_count = len(
        {(row["session_id"], row["turn_id"]) for row in rows if row["turn_id"]}
    )
    tool_rows = _tool_rows(conn, start, end, project=project)
    cards.tool_call_count = len(tool_rows)
    failures = sum(1 for row in tool_rows if _is_failure(row))
    cards.tool_failure_rate = failures / len(tool_rows) if tool_rows else 0.0
    return OverviewResponse(
        range=_range_info(start, end, days),
        cards=cards,
        daily=_daily(rows, costed),
        projects=_project_stats(rows, costed, tool_rows),
    )


def _project_stats(
    rows: list[sqlite3.Row], costed: _CostedRows, tool_rows: list[sqlite3.Row]
) -> list[ProjectStat]:
    stats: dict[str, ProjectStat] = {}
    cached_by_project: dict[str, int] = {}
    for row in rows:
        name = row["project"] or "未归类"
        stat = stats.setdefault(name, ProjectStat(project=name))
        value, _ = costed.cost(row)
        stat.cost += value
        stat.input_tokens += _row_tokens(row, "input_tokens")
        stat.output_tokens += _row_tokens(row, "output_tokens")
        stat.api_call_count += 1
        cached_by_project[name] = cached_by_project.get(name, 0) + _row_tokens(
            row, "cached_input_tokens"
        )
    for stat in stats.values():
        stat.total_tokens = stat.input_tokens + stat.output_tokens
        stat.cache_hit_rate = _cache_hit_rate(
            cached_by_project.get(stat.project, 0), stat.input_tokens
        )
    per_project_tools: dict[str, list[sqlite3.Row]] = {}
    for row in tool_rows:
        per_project_tools.setdefault(row["project"] or "未归类", []).append(row)
        stats.setdefault(
            row["project"] or "未归类", ProjectStat(project=row["project"] or "未归类")
        )
    sessions_by_project: dict[str, set[str]] = {}
    for row in rows:
        if row["session_id"]:
            sessions_by_project.setdefault(row["project"] or "未归类", set()).add(row["session_id"])
    for name, stat in stats.items():
        stat.session_count = len(sessions_by_project.get(name, set()))
        tools = per_project_tools.get(name, [])
        failures = sum(1 for row in tools if _is_failure(row))
        stat.tool_failure_rate = failures / len(tools) if tools else 0.0
    return sorted(stats.values(), key=lambda item: item.cost, reverse=True)


def list_projects(
    conn: sqlite3.Connection, *, days: int = 30, now: datetime | None = None
) -> list[ProjectStat]:
    return overview(conn, days=days, now=now).projects


def project_detail(
    conn: sqlite3.Connection, name: str, *, days: int = 30, now: datetime | None = None
) -> ProjectDetail:
    start, end = _range_bounds(now, days)
    costed = _CostedRows(conn, now=now)
    rows = _fetch_api_rows(conn, start, end, project=name)
    tool_rows = _tool_rows(conn, start, end, project=name)
    stats = _project_stats(rows, costed, tool_rows)
    stat = next((item for item in stats if item.project == name), ProjectStat(project=name))
    return ProjectDetail(
        range=_range_info(start, end, days),
        project=stat,
        daily=_daily(rows, costed),
        sessions=_session_summaries(conn, rows, costed),
        cost_breakdown=_breakdown(rows, costed),
    )


def _session_summaries(
    conn: sqlite3.Connection, rows: list[sqlite3.Row], costed: _CostedRows
) -> list[SessionSummary]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if row["session_id"]:
            grouped.setdefault(row["session_id"], []).append(row)
    tool_counts = _tool_counts_by_session(conn, list(grouped))
    summaries: list[SessionSummary] = []
    for session_id, group in grouped.items():
        meta = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        summary = SessionSummary(
            session_id=session_id,
            project=(meta["project"] if meta else group[0]["project"]) or "未归类",
            cwd=meta["cwd"] if meta else None,
            cli_version=meta["cli_version"] if meta else None,
            model_provider=meta["model_provider"] if meta else None,
            first_seen_at=meta["first_seen_at"] if meta else None,
            updated_at=meta["updated_at"] if meta else None,
            recorded_at=meta["recorded_at"] if meta else None,
            turn_count=len(_turn_ids(conn, session_id)),
            tool_call_count=tool_counts.get(session_id, 0),
        )
        for row in group:
            value, _ = costed.cost(row)
            summary.cost += value
            summary.total_tokens += _row_tokens(row, "input_tokens") + _row_tokens(
                row, "output_tokens"
            )
            summary.api_call_count += 1
        cached = sum(_row_tokens(row, "cached_input_tokens") for row in group)
        inputs = sum(_row_tokens(row, "input_tokens") for row in group)
        summary.cache_hit_rate = _cache_hit_rate(cached, inputs)
        stamps = sorted(str(row["occurred_at"]) for row in group if row["occurred_at"])
        summary.first_api_at = stamps[0] if stamps else None
        summary.last_api_at = stamps[-1] if stamps else None
        summaries.append(summary)
    return sorted(summaries, key=lambda item: item.cost, reverse=True)


def _tool_counts_by_session(
    conn: sqlite3.Connection, session_ids: list[str]
) -> dict[str, int]:
    """一次查出每个会话的工具调用条数，避免逐会话再查一遍。"""
    if not session_ids:
        return {}
    placeholders = ", ".join("?" for _ in session_ids)
    rows = conn.execute(
        f"SELECT session_id, COUNT(*) AS n FROM tool_call_details "
        f"WHERE session_id IN ({placeholders}) GROUP BY session_id",
        session_ids,
    ).fetchall()
    return {row["session_id"]: int(row["n"]) for row in rows}


def _turn_ids(conn: sqlite3.Connection, session_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT turn_id FROM turns WHERE session_id = ? ORDER BY started_at, turn_id",
        (session_id,),
    ).fetchall()
    return [row["turn_id"] for row in rows]


def list_sessions(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    days: int = 30,
    now: datetime | None = None,
) -> Page[SessionSummary]:
    start, end = _range_bounds(now, days)
    costed = _CostedRows(conn, now=now)
    rows = _fetch_api_rows(conn, start, end, project=project)
    summaries = _session_summaries(conn, rows, costed)
    if search:
        needle = search.lower()
        summaries = [
            item
            for item in summaries
            if needle in item.session_id.lower()
            or needle in (item.cwd or "").lower()
            or needle in item.project.lower()
        ]
    total = len(summaries)
    return Page[SessionSummary](
        items=summaries[offset : offset + limit], total=total, limit=limit, offset=offset
    )


def _turn_summaries(
    conn: sqlite3.Connection,
    session_id: str,
    rows: list[sqlite3.Row],
    costed: _CostedRows,
) -> list[TurnSummary]:
    meta = {
        row["turn_id"]: row
        for row in conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY started_at, turn_id",
            (session_id,),
        ).fetchall()
    }
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if row["turn_id"]:
            grouped.setdefault(row["turn_id"], []).append(row)
    for turn_id in meta:
        grouped.setdefault(turn_id, [])
    tools_by_turn = _tools_by_turn(conn, session_id, list(grouped))

    summaries: list[TurnSummary] = []
    previous_input: int | None = None
    for index, turn_id in enumerate(grouped, start=1):
        group = grouped[turn_id]
        record = meta.get(turn_id)
        tools = tools_by_turn.get(turn_id, [])
        summary = TurnSummary(
            turn_id=turn_id,
            index=index,
            model=record["model"] if record else None,
            effort=record["effort"] if record else None,
            started_at=record["started_at"] if record else None,
            completed_at=record["completed_at"] if record else None,
            duration_ms=record["duration_ms"] if record else None,
            aborted_reason=record["aborted_reason"] if record else None,
            api_call_count=len(group),
            tool_call_count=len(tools),
            tool_failure_count=sum(1 for row in tools if _is_failure(row)),
        )
        for row in group:
            value, _ = costed.cost(row)
            summary.cost += value
            summary.input_tokens += _row_tokens(row, "input_tokens")
            summary.cached_input_tokens += _row_tokens(row, "cached_input_tokens")
            summary.output_tokens += _row_tokens(row, "output_tokens")
        summary.cache_hit_rate = _cache_hit_rate(
            summary.cached_input_tokens, summary.input_tokens
        )
        if previous_input:
            summary.context_growth = summary.input_tokens / previous_input
        if summary.input_tokens:
            previous_input = summary.input_tokens
        summaries.append(summary)
    return summaries


def _tools_by_turn(
    conn: sqlite3.Connection, session_id: str, turn_ids: list[str]
) -> dict[str, list[sqlite3.Row]]:
    """按「同一文件里其后第一条 api_call 的轮次」把工具调用归到轮次上。"""
    anchors: dict[str, list[tuple[int, str]]] = {}
    for row in conn.execute(
        "SELECT file_path, ordinal, turn_id FROM api_calls "
        "WHERE session_id = ? AND turn_id IS NOT NULL ORDER BY file_path, ordinal",
        (session_id,),
    ).fetchall():
        anchors.setdefault(row["file_path"], []).append((row["ordinal"], row["turn_id"]))

    result: dict[str, list[sqlite3.Row]] = {turn_id: [] for turn_id in turn_ids}
    for row in conn.execute(
        "SELECT * FROM tool_call_details WHERE session_id = ?", (session_id,)
    ).fetchall():
        pairs = anchors.get(row["file_path"])
        if not pairs:
            continue
        ordinals = [item[0] for item in pairs]
        index = bisect_right(ordinals, row["call_ordinal"])
        if index >= len(ordinals):
            index = len(ordinals) - 1
        turn_id = pairs[index][1]
        result.setdefault(turn_id, []).append(row)
    return result


def session_detail(
    conn: sqlite3.Connection, session_id: str, *, now: datetime | None = None
) -> SessionDetail | None:
    meta = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if meta is None:
        return None
    costed = _CostedRows(conn, now=now)
    rows = _fetch_api_rows(
        conn,
        datetime.min.replace(tzinfo=UTC),
        datetime.max.replace(tzinfo=UTC),
        session_id=session_id,
    )
    summaries = _session_summaries(conn, rows, costed)
    session = (
        summaries[0]
        if summaries
        else SessionSummary(session_id=session_id, project=meta["project"])
    )
    return SessionDetail(
        session=session,
        turns=_turn_summaries(conn, session_id, rows, costed),
        cost_breakdown=_breakdown(rows, costed),
    )


def turn_detail(
    conn: sqlite3.Connection, session_id: str, turn_id: str, *, now: datetime | None = None
) -> TurnDetail | None:
    detail = session_detail(conn, session_id, now=now)
    if detail is None:
        return None
    turn = next((item for item in detail.turns if item.turn_id == turn_id), None)
    if turn is None:
        return None
    costed = _CostedRows(conn, now=now)
    rows = conn.execute(
        "SELECT * FROM api_call_view WHERE session_id = ? AND turn_id = ? ORDER BY ordinal",
        (session_id, turn_id),
    ).fetchall()
    api_calls = []
    for row in rows:
        value, priced = costed.cost(row)
        api_calls.append(
            ApiCallRow(
                ordinal=row["ordinal"],
                file_path=row["file_path"],
                response_id=row["response_id"],
                timestamp=row["timestamp"],
                model=row["model"],
                input_tokens=_row_tokens(row, "input_tokens"),
                cached_input_tokens=_row_tokens(row, "cached_input_tokens"),
                output_tokens=_row_tokens(row, "output_tokens"),
                reasoning_output_tokens=_row_tokens(row, "reasoning_output_tokens"),
                total_tokens=_row_tokens(row, "total_tokens"),
                cache_hit_rate=_cache_hit_rate(
                    _row_tokens(row, "cached_input_tokens"), _row_tokens(row, "input_tokens")
                ),
                cost=value,
                priced=priced,
            )
        )
    tool_calls = [
        _tool_call_row(row)
        for row in _tools_by_turn(conn, session_id, [turn_id]).get(turn_id, [])
    ]
    tool_calls.sort(key=lambda item: item.ordinal)
    return TurnDetail(turn=turn, api_calls=api_calls, tool_calls=tool_calls)


def _tool_call_row(row: sqlite3.Row) -> ToolCallRow:
    success = None if row["success"] is None else bool(row["success"])
    return ToolCallRow(
        ordinal=row["call_ordinal"],
        call_id=row["call_id"],
        name=row["name"],
        kind=row["kind"],
        arguments_chars=row["arguments_chars"] or 0,
        output_chars=row["output_chars"] or 0,
        exit_code=row["exit_code"],
        wall_time_seconds=row["wall_time_seconds"],
        success=success,
        duration_ms=_duration_ms(row),
        result_summary=row["result_summary"],
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def tool_stats(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    now: datetime | None = None,
) -> ToolsResponse:
    start, end = _range_bounds(now, days)
    rows = _tool_rows(conn, start, end, project=project)
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(row)
    stats = []
    for name, group in grouped.items():
        durations = [
            float(_duration_ms(row)) for row in group if _duration_ms(row) is not None
        ]
        failures = sum(1 for row in group if _is_failure(row))
        stats.append(
            ToolStat(
                name=name,
                call_count=len(group),
                failure_count=failures,
                failure_rate=failures / len(group),
                avg_duration_ms=(sum(durations) / len(durations)) if durations else None,
                p95_duration_ms=_percentile(durations, 0.95),
                total_output_chars=sum(int(row["output_chars"] or 0) for row in group),
            )
        )
    stats.sort(key=lambda item: item.call_count, reverse=True)
    failures_page = _failure_page(conn, start, end, project, limit=50, offset=0)
    by_project: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_project.setdefault(row["project"] or "未归类", []).append(row)
    return ToolsResponse(
        range=_range_info(start, end, days),
        stats=stats,
        failures=failures_page.items,
        failure_cost=failure_cost(conn, days=days, project=project, now=now),
        by_project=[
            ProjectToolFailure(
                project=name,
                call_count=len(group),
                failure_count=sum(1 for row in group if _is_failure(row)),
                failure_rate=(
                    sum(1 for row in group if _is_failure(row)) / len(group) if group else 0.0
                ),
            )
            for name, group in sorted(by_project.items())
        ],
    )


def _failure_page(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    project: str | None,
    *,
    limit: int,
    offset: int,
) -> Page[ToolFailure]:
    rows = [row for row in _tool_rows(conn, start, end, project=project) if _is_failure(row)]
    failures = [
        ToolFailure(
            session_id=row["session_id"] or "",
            turn_id=None,
            project=row["project"] or "未归类",
            call_id=row["call_id"],
            name=row["name"],
            exit_code=row["exit_code"],
            wall_time_seconds=row["wall_time_seconds"],
            result_summary=row["result_summary"],
            ordinal=row["call_ordinal"],
            file_path=row["file_path"],
        )
        for row in rows
    ]
    return Page[ToolFailure](
        items=failures[offset : offset + limit], total=len(failures), limit=limit, offset=offset
    )


def tool_failures(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> Page[ToolFailure]:
    start, end = _range_bounds(now, days)
    return _failure_page(conn, start, end, project, limit=limit, offset=offset)


def failure_cost(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    now: datetime | None = None,
) -> CostBreakdown:
    """工具失败折算成本（设计文档 6.6 节）。

    定义：某个 turn 内出现了工具失败时，该轮**首次失败之后**的 api_call 视为重试，
    把它们的成本算作失败成本。因为 api_call 没有调用级时间戳（4.11），这里用
    「同一文件里 ordinal 大于首次失败工具调用」来近似「之后」。
    """
    start, end = _range_bounds(now, days)
    costed = _CostedRows(conn, now=now)
    rows = _fetch_api_rows(conn, start, end, project=project)
    tool_rows = _tool_rows(conn, start, end, project=project)

    first_failure: dict[tuple[str | None, str], int] = {}
    for row in tool_rows:
        if not _is_failure(row):
            continue
        key = (row["session_id"], row["file_path"])
        ordinal = row["call_ordinal"]
        if key not in first_failure or ordinal < first_failure[key]:
            first_failure[key] = ordinal
    if not first_failure:
        return CostBreakdown()

    retries = [
        row
        for row in rows
        if (row["session_id"], row["file_path"]) in first_failure
        and row["ordinal"] > first_failure[(row["session_id"], row["file_path"])]
    ]
    breakdown = _breakdown(retries, costed)
    return breakdown
