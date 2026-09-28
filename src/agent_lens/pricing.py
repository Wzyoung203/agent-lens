"""成本计算：价目表读写与按 token 用量估算费用。

设计约定（设计文档 6.4 节）：
  * 成本不落库，查询时计算，所以改价后历史能一致重算
  * pricing 每条带 effective_from，支持「按当时价目表」与「按当前价目表」两种口径
  * 价格单位统一为「每百万 token 的金额」（price per million tokens）
  * reasoning_output_tokens 已包含在 output_tokens 内（4.4 节实测），
    未单独定价时并入 output 价计算，绝不重复相加
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from .models import TokenUsage

PRICE_SCALE = 1_000_000
DEFAULT_CURRENCY = "USD"

# deepseek 按「高峰 / 空闲」两档计价，空闲价一律是高峰价的一半（设计文档 6.4）。
# 高峰时段为 UTC 周一至周五的 01:00-04:00 与 06:00-10:00；其余全部按空闲价。
TimeWindow = Literal["any", "peak", "idle"]
PEAK_RANGES: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))

# 官方遗留模型名到规范名的映射。实测日志里出现过 deepseek-v4-flash，
# 实际按 Flash 价计价；查价前先归一化，否则会落到「查不到价目表」。
MODEL_ALIASES: dict[str, str] = {"deepseek-v4-flash": "deepseek-flash"}


def is_peak(at: datetime) -> bool:
    """判断某个 UTC 时刻是否落在 deepseek 高峰计价时段。

    简化说明：不实现中国法定节假日日历（设计文档 6.4 列为可选）。工作日里的节假日
    （如国庆）本应算空闲，这里仍按高峰处理，属已知偏差。
    """
    moment = at.astimezone(UTC)
    if moment.weekday() >= 5:  # 周六、周日全天空闲
        return False
    return any(start <= moment.hour < end for start, end in PEAK_RANGES)


def normalize_model(model: str | None) -> str | None:
    """把遗留模型名归一化成当前价目表里的规范名，未知名原样返回。"""
    if model is None:
        return None
    return MODEL_ALIASES.get(model, model)


class PriceEntry(BaseModel):
    """一个 (provider, model) 在某个生效时刻、某个时段的价格。"""

    provider: str
    model: str
    effective_from: datetime
    time_window: TimeWindow = "any"
    input_price_per_mtok: float
    cached_input_price_per_mtok: float
    output_price_per_mtok: float
    reasoning_output_price_per_mtok: float | None = None
    currency: str = DEFAULT_CURRENCY


class CostSummary(BaseModel):
    """一次成本汇总。unpriced_calls 用来暴露「有调用但查不到价目表」。"""

    total: float
    currency: str = ""
    priced_calls: int = 0
    unpriced_calls: int = 0


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def estimate_cost(usage: TokenUsage, price: PriceEntry) -> float:
    """按一次调用的 token 用量与价目表算钱。

    (input - cached) 走未命中价，cached 走命中价，
    output 里的非 reasoning 部分走 output 价，reasoning 部分走它自己的价
    （没配就并入 output 价）。
    """
    reasoning_tokens = usage.reasoning_output_tokens
    plain_output_tokens = usage.output_tokens - reasoning_tokens
    reasoning_price = price.reasoning_output_price_per_mtok
    if reasoning_price is None:
        reasoning_price = price.output_price_per_mtok
    billed = (
        usage.uncached_input_tokens * price.input_price_per_mtok
        + usage.cached_input_tokens * price.cached_input_price_per_mtok
        + plain_output_tokens * price.output_price_per_mtok
        + reasoning_tokens * reasoning_price
    )
    return billed / PRICE_SCALE


def upsert_price(conn: sqlite3.Connection, entry: PriceEntry) -> None:
    """写入一条价目表记录。同一 (provider, model, effective_from, time_window) 重复写入即修正。"""
    with conn:
        conn.execute(
            """
            INSERT INTO pricing (
                provider, model, effective_from, time_window,
                input_price_per_mtok, cached_input_price_per_mtok,
                output_price_per_mtok, reasoning_output_price_per_mtok, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, model, effective_from, time_window) DO UPDATE SET
                input_price_per_mtok = excluded.input_price_per_mtok,
                cached_input_price_per_mtok = excluded.cached_input_price_per_mtok,
                output_price_per_mtok = excluded.output_price_per_mtok,
                reasoning_output_price_per_mtok = excluded.reasoning_output_price_per_mtok,
                currency = excluded.currency
            """,
            (
                entry.provider,
                entry.model,
                to_iso(entry.effective_from),
                entry.time_window,
                entry.input_price_per_mtok,
                entry.cached_input_price_per_mtok,
                entry.output_price_per_mtok,
                entry.reasoning_output_price_per_mtok,
                entry.currency,
            ),
        )


def _row_to_entry(row: sqlite3.Row) -> PriceEntry:
    return PriceEntry(
        provider=row["provider"],
        model=row["model"],
        effective_from=datetime.fromisoformat(row["effective_from"]),
        time_window=row["time_window"],
        input_price_per_mtok=row["input_price_per_mtok"],
        cached_input_price_per_mtok=row["cached_input_price_per_mtok"],
        output_price_per_mtok=row["output_price_per_mtok"],
        reasoning_output_price_per_mtok=row["reasoning_output_price_per_mtok"],
        currency=row["currency"],
    )


def list_prices(
    conn: sqlite3.Connection,
    provider: str | None = None,
    model: str | None = None,
) -> list[PriceEntry]:
    sql = "SELECT * FROM pricing"
    params: list[str] = []
    filters = []
    if provider is not None:
        filters.append("provider = ?")
        params.append(provider)
    if model is not None:
        filters.append("model = ?")
        params.append(model)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY provider, model, effective_from"
    return [_row_to_entry(row) for row in conn.execute(sql, params).fetchall()]


def price_at(
    conn: sqlite3.Connection,
    provider: str,
    model: str,
    *,
    at: datetime | None = None,
    now: datetime | None = None,
    time_window: TimeWindow | None = None,
) -> PriceEntry | None:
    """取生效时间不晚于 at 的最新一条价目表，按时段优先匹配。

    选价顺序（设计文档 6.4 约束 1）：
        (归一化名, 命中时段) -> (归一化名, any) -> (原始名, 命中时段) -> (原始名, any)
    at 为空表示「按当前价目表」，即以 now（默认系统当前时间）为基准；
    time_window 为空时按 at 自行判定 peak / idle。
    """
    moment = at or now or datetime.now(UTC)
    window: TimeWindow = time_window or ("peak" if is_peak(moment) else "idle")
    return _lookup_price(conn, provider, model, window=window, bound=moment)


def _candidate_keys(model: str | None, window: TimeWindow) -> list[tuple[str, str]]:
    """按时段优先、归一化优先的顺序给出 (model, time_window) 候选键。"""
    names: list[str] = []
    normalized = normalize_model(model)
    if normalized is not None:
        names.append(normalized)
    if model is not None and model not in names:
        names.append(model)
    windows = [window] if window == "any" else [window, "any"]
    keys: list[tuple[str, str]] = []
    for name in names:
        for win in windows:
            key = (name, win)
            if key not in keys:
                keys.append(key)
    return keys


def _lookup_price(
    conn: sqlite3.Connection,
    provider: str,
    model: str | None,
    *,
    window: TimeWindow,
    bound: datetime,
) -> PriceEntry | None:
    """按候选顺序取生效时间不晚于 bound 的最新一条价目表，全部未命中返回 None。"""
    for name, win in _candidate_keys(model, window):
        row = conn.execute(
            """
            SELECT * FROM pricing
            WHERE provider = ? AND model = ? AND time_window = ? AND effective_from <= ?
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            (provider, name, win, to_iso(bound)),
        ).fetchone()
        if row is not None:
            return _row_to_entry(row)
    return None


class Pricer:
    """按行计价的小工具，带查价缓存。

    at_call_time=False（默认）表示「按当前价目表重算」：金额取当前生效的价目表，
    但时段仍由调用发生时刻决定（peak / idle 是调用本身的属性）。
    at_call_time=True 表示「按当时价目表计价」：连生效时间也以调用时刻为准。
    时段一律从行的 timestamp 判定；该列缺失时按 now 判定。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        at_call_time: bool = False,
        now: datetime | None = None,
    ) -> None:
        self._conn = conn
        self._at_call_time = at_call_time
        self._now = now or datetime.now(UTC)
        self._cache: dict[tuple[str | None, str, str, str], PriceEntry | None] = {}

    def price_for(
        self, provider: str | None, model: str | None, at: datetime | None = None
    ) -> PriceEntry | None:
        """取某次调用适用的价目表；缓存键含 providers/model/时段/生效上界。"""
        if not provider or not model:
            return None
        window: TimeWindow = "peak" if is_peak(at or self._now) else "idle"
        bound = at if (self._at_call_time and at is not None) else self._now
        key = (provider, model, window, to_iso(bound))
        if key not in self._cache:
            self._cache[key] = _lookup_price(
                self._conn, provider, model, window=window, bound=bound
            )
        return self._cache[key]

    def cost_for(self, row) -> float | None:
        """按一行 api_call 算钱：用量为零返回 0.0，查不到价目表返回 None。

        row 需含 api_call_view 的列：model_provider / model / timestamp 与六个 token 列。
        """
        input_tokens = row["input_tokens"] or 0
        output_tokens = row["output_tokens"] or 0
        if input_tokens == 0 and output_tokens == 0:
            return 0.0
        at = None
        timestamp = row["timestamp"]
        if timestamp:
            at = datetime.fromisoformat(timestamp)
        price = self.price_for(row["model_provider"], row["model"], at)
        if price is None:
            return None
        usage = TokenUsage(
            input_tokens=input_tokens,
            cached_input_tokens=row["cached_input_tokens"] or 0,
            cache_write_input_tokens=row["cache_write_input_tokens"] or 0,
            output_tokens=output_tokens,
            reasoning_output_tokens=row["reasoning_output_tokens"] or 0,
            total_tokens=row["total_tokens"] or 0,
        )
        return estimate_cost(usage, price)


def summarize_cost(
    conn: sqlite3.Connection,
    *,
    provider: str,
    model: str | None = None,
    at_call_time: bool = False,
    now: datetime | None = None,
) -> CostSummary:
    """汇总所有 api_call 的成本。

    model 为空时按每个 turn 自己的模型查价；at_call_time=True 用调用发生时刻的
    价目表（历史精确计价），否则全部用当前价目表重算。
    """
    rows = conn.execute("SELECT * FROM api_call_view ORDER BY timestamp").fetchall()
    total = 0.0
    currency = ""
    priced = 0
    unpriced = 0
    cache: dict[tuple[str, str], PriceEntry | None] = {}

    for row in rows:
        call_model = model or row["model"]
        call_at = None
        if at_call_time:
            if not row["timestamp"]:
                unpriced += 1
                continue
            call_at = datetime.fromisoformat(row["timestamp"])
        if not call_model:
            unpriced += 1
            continue

        cache_key = (call_model, call_at.isoformat() if call_at else "")
        if cache_key not in cache:
            cache[cache_key] = price_at(conn, provider, call_model, at=call_at, now=now)
        price = cache[cache_key]
        if price is None:
            unpriced += 1
            continue

        usage = TokenUsage(
            input_tokens=row["input_tokens"],
            cached_input_tokens=row["cached_input_tokens"],
            cache_write_input_tokens=row["cache_write_input_tokens"],
            output_tokens=row["output_tokens"],
            reasoning_output_tokens=row["reasoning_output_tokens"],
            total_tokens=row["total_tokens"],
        )
        total += estimate_cost(usage, price)
        currency = price.currency
        priced += 1

    return CostSummary(
        total=total, currency=currency, priced_calls=priced, unpriced_calls=unpriced
    )
