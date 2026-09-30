"""展示币种：价目表按原币种存，算钱时折成配置的展示币种。"""

from datetime import UTC, datetime

import pytest

from agent_lens import queries
from agent_lens.config import AppConfig, DisplayConfig
from agent_lens.pricing import (
    PriceEntry,
    price_at,
    set_display_config,
    upsert_price,
)


def _price(currency: str) -> PriceEntry:
    return PriceEntry(
        provider="deepseek",
        model="demo",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        input_price_per_mtok=1.0,
        cached_input_price_per_mtok=0.1,
        output_price_per_mtok=2.0,
        currency=currency,
    )


def test_default_display_is_cny():
    assert AppConfig().display.currency == "CNY"
    assert AppConfig().display.usd_to_cny == pytest.approx(7.1)


def test_usd_prices_become_cny_when_display_is_cny(lens_db):
    upsert_price(lens_db, _price("USD"))
    set_display_config(DisplayConfig(currency="CNY", usd_to_cny=7.0))

    entry = price_at(lens_db, "deepseek", "demo", now=datetime(2026, 9, 1, tzinfo=UTC))

    assert entry is not None
    assert entry.currency == "CNY"
    assert entry.input_price_per_mtok == pytest.approx(7.0)
    assert entry.cached_input_price_per_mtok == pytest.approx(0.7)
    assert entry.output_price_per_mtok == pytest.approx(14.0)


def test_usd_display_keeps_the_stored_numbers(lens_db):
    upsert_price(lens_db, _price("USD"))
    set_display_config(DisplayConfig(currency="USD"))

    entry = price_at(lens_db, "deepseek", "demo", now=datetime(2026, 9, 1, tzinfo=UTC))

    assert entry is not None
    assert entry.currency == "USD"
    assert entry.input_price_per_mtok == pytest.approx(1.0)


def test_cny_prices_are_not_converted_twice(lens_db):
    upsert_price(lens_db, _price("CNY"))
    set_display_config(DisplayConfig(currency="CNY", usd_to_cny=7.0))

    entry = price_at(lens_db, "deepseek", "demo", now=datetime(2026, 9, 1, tzinfo=UTC))

    assert entry is not None
    assert entry.currency == "CNY"
    assert entry.input_price_per_mtok == pytest.approx(1.0)


def test_overview_totals_follow_the_display_rate(seeded_db):
    """端到端：同一份数据在 CNY 下应当正好是 USD 的 7 倍。"""
    set_display_config(DisplayConfig(currency="USD"))
    usd = queries.overview(seeded_db, days=3650).cards.total_cost

    set_display_config(DisplayConfig(currency="CNY", usd_to_cny=7.0))
    cny = queries.overview(seeded_db, days=3650)

    assert cny.cards.currency == "CNY"
    assert cny.cards.total_cost == pytest.approx(usd * 7.0)


def test_project_and_session_payloads_carry_the_display_currency(seeded_db):
    set_display_config(DisplayConfig(currency="CNY", usd_to_cny=7.0))

    detail = queries.project_detail(seeded_db, "alpha", days=3650)

    assert detail.project.currency == "CNY"
    assert detail.cost_breakdown.currency == "CNY"
    assert {item.currency for item in detail.sessions} == {"CNY"}
