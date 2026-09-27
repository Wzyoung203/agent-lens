import pytest

from agent_lens.models import TokenUsage, to_utc
from agent_lens.pricing import PriceEntry, estimate_cost, price_at, summarize_cost, upsert_price
from agent_lens.storage import write_parsed_session
from tests.test_storage_write import minimal_session


def price(input_price=1.0, cached=0.1, output=2.0, reasoning=None, when="2026-09-01T00:00:00Z"):
    return PriceEntry(
        provider="deepseek",
        model="deepseek-flash",
        effective_from=to_utc(when),
        input_price_per_mtok=input_price,
        cached_input_price_per_mtok=cached,
        output_price_per_mtok=output,
        reasoning_output_price_per_mtok=reasoning,
    )


def test_cost_splits_cached_and_uncached():
    usage = TokenUsage(input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=100_000)

    cost = estimate_cost(usage, price())

    assert cost == pytest.approx(0.6 * 1.0 + 0.4 * 0.1 + 0.1 * 2.0)


def test_reasoning_tokens_are_not_double_billed():
    usage = TokenUsage(output_tokens=100_000, reasoning_output_tokens=40_000)

    without_own_price = estimate_cost(usage, price())
    with_own_price = estimate_cost(usage, price(reasoning=0.5))

    assert without_own_price == pytest.approx(0.1 * 2.0)
    assert with_own_price == pytest.approx(0.06 * 2.0 + 0.04 * 0.5)


def test_price_lookup_respects_effective_from(lens_db):
    old = price(input_price=1.0, when="2026-08-01T00:00:00Z")
    new = price(input_price=2.0, when="2026-09-20T00:00:00Z")
    upsert_price(lens_db, old)
    upsert_price(lens_db, new)

    assert price_at(lens_db, "deepseek", "deepseek-flash", at=to_utc("2026-09-10T00:00:00Z")) == old
    assert price_at(lens_db, "deepseek", "deepseek-flash", at=to_utc("2026-09-25T00:00:00Z")) == new
    assert price_at(lens_db, "deepseek", "deepseek-flash", at=to_utc("2026-07-01T00:00:00Z")) is None


def test_upsert_price_corrects_same_effective_from(lens_db):
    upsert_price(lens_db, price(input_price=1.0))
    upsert_price(lens_db, price(input_price=3.0))

    row = lens_db.execute(
        "SELECT COUNT(*) AS n, MAX(input_price_per_mtok) AS p FROM pricing"
    ).fetchone()

    assert row["n"] == 1
    assert row["p"] == pytest.approx(3.0)


def test_cost_is_recomputed_after_price_change(lens_db):
    write_parsed_session(lens_db, minimal_session())
    upsert_price(lens_db, price(input_price=1.0, cached=1.0, output=0.0))
    before = summarize_cost(lens_db, provider="deepseek", now=to_utc("2026-09-27T12:00:00Z"))
    upsert_price(lens_db, price(input_price=2.0, cached=2.0, output=0.0))
    after = summarize_cost(lens_db, provider="deepseek", now=to_utc("2026-09-27T12:00:00Z"))

    assert before.total == pytest.approx(100 * 1.0 / 1_000_000)
    assert after.total == pytest.approx(before.total * 2)
    assert before.priced_calls == 1


def test_historical_pricing_uses_call_time(lens_db):
    parsed = minimal_session()
    parsed.api_calls[0].timestamp = to_utc("2026-08-10T00:00:00Z")
    write_parsed_session(lens_db, parsed)
    upsert_price(lens_db, price(input_price=1.0, cached=1.0, output=0.0, when="2026-08-01T00:00:00Z"))
    upsert_price(lens_db, price(input_price=5.0, cached=5.0, output=0.0, when="2026-09-01T00:00:00Z"))

    current = summarize_cost(
        lens_db, provider="deepseek", at_call_time=False, now=to_utc("2026-09-27T12:00:00Z")
    )
    historical = summarize_cost(
        lens_db, provider="deepseek", at_call_time=True, now=to_utc("2026-09-27T12:00:00Z")
    )

    assert current.total == pytest.approx(100 * 5.0 / 1_000_000)
    assert historical.total == pytest.approx(100 * 1.0 / 1_000_000)


def test_unpriced_calls_are_reported(lens_db):
    write_parsed_session(lens_db, minimal_session())

    summary = summarize_cost(lens_db, provider="deepseek")

    assert summary.priced_calls == 0
    assert summary.unpriced_calls == 1
    assert summary.total == 0.0
