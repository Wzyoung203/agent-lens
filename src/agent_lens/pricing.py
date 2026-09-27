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

from pydantic import BaseModel

from .models import TokenUsage

PRICE_SCALE = 1_000_000
DEFAULT_CURRENCY = "USD"


class PriceEntry(BaseModel):
    """一个 (provider, model) 在某个生效时刻的价格。"""

    provider: str
    model: str
    effective_from: datetime
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
    """写入一条价目表记录。同一 (provider, model, effective_from) 重复写入即修正。"""
    with conn:
        conn.execute(
            """
            INSERT INTO pricing (
                provider, model, effective_from,
                input_price_per_mtok, cached_input_price_per_mtok,
                output_price_per_mtok, reasoning_output_price_per_mtok, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, model, effective_from) DO UPDATE SET
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
) -> PriceEntry | None:
    """取生效时间不晚于 at 的最新一条价目表。

    at 为空表示「按当前价目表」，即以 now（默认系统当前时间）为基准。
    """
    moment = at or now or datetime.now(UTC)
    row = conn.execute(
        """
        SELECT * FROM pricing
        WHERE provider = ? AND model = ? AND effective_from <= ?
        ORDER BY effective_from DESC
        LIMIT 1
        """,
        (provider, model, to_iso(moment)),
    ).fetchone()
    return _row_to_entry(row) if row is not None else None


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
