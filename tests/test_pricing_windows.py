from datetime import UTC, datetime

from agent_lens import pricing
from agent_lens.pricing import PriceEntry, Pricer, price_at, upsert_price


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _price(
    window: str,
    *,
    model: str = "deepseek-flash",
    input_price: float = 1.0,
    cached: float = 0.1,
    output: float = 2.0,
    when: str = "2026-01-01T00:00:00+00:00",
) -> PriceEntry:
    return PriceEntry(
        provider="deepseek",
        model=model,
        effective_from=_at(when),
        time_window=window,
        input_price_per_mtok=input_price,
        cached_input_price_per_mtok=cached,
        output_price_per_mtok=output,
    )


def test_is_peak_weekday_windows():
    assert pricing.is_peak(_at("2026-09-28T01:00:00+00:00")) is True  # 周一
    assert pricing.is_peak(_at("2026-09-28T03:59:59+00:00")) is True
    assert pricing.is_peak(_at("2026-09-28T04:00:00+00:00")) is False
    assert pricing.is_peak(_at("2026-09-28T06:00:00+00:00")) is True
    assert pricing.is_peak(_at("2026-09-28T10:00:00+00:00")) is False
    assert pricing.is_peak(_at("2026-09-28T12:00:00+00:00")) is False


def test_is_peak_weekend_is_idle():
    assert pricing.is_peak(_at("2026-09-26T02:00:00+00:00")) is False  # 周六
    assert pricing.is_peak(_at("2026-09-27T07:00:00+00:00")) is False  # 周日


def test_is_peak_ignores_non_utc_input():
    # 北京时间 2026-09-28T09:30 = UTC 01:30，落在高峰时段内
    assert pricing.is_peak(_at("2026-09-28T09:30:00+08:00")) is True


def test_normalize_model_aliases():
    assert pricing.normalize_model("deepseek-v4-flash") == "deepseek-flash"
    assert pricing.normalize_model("deepseek-flash") == "deepseek-flash"
    assert pricing.normalize_model("unknown") == "unknown"
    assert pricing.normalize_model(None) is None


def test_price_at_selects_by_time_window(lens_db):
    upsert_price(lens_db, _price("peak", input_price=6.0))
    upsert_price(lens_db, _price("idle", input_price=3.0))

    peak = price_at(lens_db, "deepseek", "deepseek-flash", at=_at("2026-09-28T02:00:00+00:00"))
    idle = price_at(lens_db, "deepseek", "deepseek-flash", at=_at("2026-09-27T02:00:00+00:00"))

    assert peak is not None and peak.time_window == "peak"
    assert peak.input_price_per_mtok == 6.0
    assert idle is not None and idle.time_window == "idle"
    assert idle.input_price_per_mtok == 3.0


def test_price_at_falls_back_to_any_window(lens_db):
    upsert_price(lens_db, _price("any", input_price=1.0))

    peak = price_at(lens_db, "deepseek", "deepseek-flash", at=_at("2026-09-28T02:00:00+00:00"))
    idle = price_at(lens_db, "deepseek", "deepseek-flash", at=_at("2026-09-27T02:00:00+00:00"))

    assert peak is not None and peak.time_window == "any"
    assert idle is not None and idle.time_window == "any"


def test_price_at_returns_none_when_missing(lens_db):
    assert price_at(lens_db, "deepseek", "deepseek-flash", at=_at("2026-09-28T02:00:00+00:00")) is None


def test_price_at_normalizes_legacy_model_name(lens_db):
    upsert_price(lens_db, _price("any", model="deepseek-flash", input_price=1.0))

    found = price_at(lens_db, "deepseek", "deepseek-v4-flash", at=_at("2026-09-27T02:00:00+00:00"))

    assert found is not None
    assert found.model == "deepseek-flash"


def test_price_at_honours_explicit_time_window(lens_db):
    upsert_price(lens_db, _price("peak", input_price=6.0))
    upsert_price(lens_db, _price("idle", input_price=3.0))

    # 调用时刻是周末（本该选 idle），显式指定 peak 时仍应拿高峰价
    forced = price_at(
        lens_db,
        "deepseek",
        "deepseek-flash",
        at=_at("2026-09-27T02:00:00+00:00"),
        time_window="peak",
    )

    assert forced is not None and forced.time_window == "peak"


def test_price_at_prefers_normalized_window_over_raw_any(lens_db):
    # 归一化名 + 命中时段 优先于 原始名 + any
    upsert_price(lens_db, _price("peak", model="deepseek-flash", input_price=6.0))
    upsert_price(lens_db, _price("any", model="deepseek-v4-flash", input_price=99.0))

    found = price_at(lens_db, "deepseek", "deepseek-v4-flash", at=_at("2026-09-28T02:00:00+00:00"))

    assert found is not None
    assert found.model == "deepseek-flash"
    assert found.input_price_per_mtok == 6.0


def _row(**overrides) -> dict:
    row = {
        "model_provider": "deepseek",
        "model": "deepseek-flash",
        "timestamp": "2026-09-27T02:00:00+00:00",  # 周日 -> idle
        "input_tokens": 1_000_000,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 1_000_000,
    }
    row.update(overrides)
    return row


def test_pricer_prices_at_the_call_window(lens_db):
    upsert_price(lens_db, _price("peak", input_price=6.0))
    upsert_price(lens_db, _price("idle", input_price=3.0))

    idle = Pricer(lens_db, at_call_time=True, now=datetime(2026, 9, 28, 12, tzinfo=UTC))
    peak_row = _row(timestamp="2026-09-28T02:00:00+00:00")

    assert idle.cost_for(_row()) == 3.0
    assert idle.cost_for(peak_row) == 6.0


def test_pricer_current_price_mode_uses_latest_entry(lens_db):
    upsert_price(lens_db, _price("idle", input_price=3.0, when="2026-01-01T00:00:00+00:00"))
    upsert_price(lens_db, _price("idle", input_price=5.0, when="2026-09-01T00:00:00+00:00"))

    pricer = Pricer(lens_db, at_call_time=False, now=datetime(2026, 9, 28, 12, tzinfo=UTC))

    # 调用发生在 2026-09-27，但按当前价目表（2026-09-01 起）计价
    assert pricer.cost_for(_row()) == 5.0


def test_pricer_returns_none_for_unpriced_and_zero_for_empty_usage(lens_db):
    pricer = Pricer(lens_db, at_call_time=True, now=datetime(2026, 9, 28, 12, tzinfo=UTC))

    assert pricer.cost_for(_row()) is None
    assert pricer.cost_for(_row(input_tokens=0, total_tokens=0)) == 0.0
