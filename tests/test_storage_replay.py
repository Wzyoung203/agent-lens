import json
from pathlib import Path

import pytest

from agent_lens.models import to_utc
from agent_lens.parser import parse_session_file
from agent_lens.pricing import PriceEntry, summarize_cost, upsert_price
from agent_lens.storage import counts, write_parsed_session

FIXTURE = Path(__file__).parent / "fixtures" / "real_session_sample.jsonl"


def test_replay_real_fixture_is_idempotent_and_priced(lens_db):
    parsed = parse_session_file(FIXTURE)
    meta = json.loads((FIXTURE.parent / "real_session_sample.meta.json").read_text("utf-8"))

    write_parsed_session(lens_db, parsed)
    model = lens_db.execute(
        "SELECT model FROM turns WHERE model IS NOT NULL LIMIT 1"
    ).fetchone()["model"]
    upsert_price(
        lens_db,
        PriceEntry(
            provider="deepseek",
            model=model,
            effective_from=to_utc("2026-01-01T00:00:00Z"),
            input_price_per_mtok=0.28,
            cached_input_price_per_mtok=0.028,
            output_price_per_mtok=0.42,
        ),
    )

    first_counts = counts(lens_db)
    first_cost = summarize_cost(lens_db, provider="deepseek")

    write_parsed_session(lens_db, parsed)

    assert counts(lens_db) == first_counts
    assert summarize_cost(lens_db, provider="deepseek").total == pytest.approx(first_cost.total)
    assert first_counts["api_calls"] == meta["api_call_count"]
    assert first_cost.priced_calls == meta["api_call_count"]
    assert first_cost.unpriced_calls == 0
    assert first_cost.total > 0


def test_replay_records_turn_window_and_tool_join(lens_db):
    parsed = parse_session_file(FIXTURE)

    write_parsed_session(lens_db, parsed)

    turn = lens_db.execute(
        "SELECT * FROM turns WHERE started_at IS NOT NULL LIMIT 1"
    ).fetchone()
    joined = lens_db.execute("SELECT COUNT(*) FROM tool_call_details").fetchone()[0]

    assert turn["started_at"] is not None
    assert turn["completed_at"] is not None
    assert joined == len(parsed.tool_calls)
