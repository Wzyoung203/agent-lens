"""P3.1：TTFT 分布查询与 /api/latency。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agent_lens import queries
from agent_lens.api.app import create_app
from agent_lens.models import ApiCallRecord, ParsedSession, TokenUsage, TurnRecord
from agent_lens.storage import write_parsed_session


def _turn_session(
    *,
    session_id: str,
    turn_id: str,
    started_at: datetime,
    ttft: int,
    duration_ms: int,
    output_tokens: int,
) -> ParsedSession:
    return ParsedSession(
        session_id=session_id,
        file_path=f"{session_id}-{turn_id}.jsonl",
        turns=[
            TurnRecord(
                turn_id=turn_id,
                started_at=started_at,
                completed_at=started_at + timedelta(milliseconds=duration_ms),
                duration_ms=duration_ms,
                time_to_first_token_ms=ttft,
            )
        ],
        api_calls=[
            ApiCallRecord(
                file_path=f"{session_id}-{turn_id}.jsonl",
                ordinal=1,
                session_id=session_id,
                turn_id=turn_id,
                response_id=f"r-{turn_id}",
                timestamp=started_at,
                usage=TokenUsage(
                    input_tokens=1000, cached_input_tokens=0, output_tokens=output_tokens
                ),
            )
        ],
    )


def _seed(lens_db) -> None:
    base = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    write_parsed_session(
        lens_db,
        _turn_session(
            session_id="s1",
            turn_id="t1",
            started_at=base,
            ttft=1000,
            duration_ms=4000,
            output_tokens=100,
        ),
    )
    write_parsed_session(
        lens_db,
        _turn_session(
            session_id="s1",
            turn_id="t2",
            started_at=base + timedelta(days=1),
            ttft=3000,
            duration_ms=9000,
            output_tokens=50,
        ),
    )


def test_latency_percentiles_and_tbt(lens_db):
    _seed(lens_db)

    result = queries.latency_stats(lens_db, days=3650)

    # 两个样本：1000 与 3000（最近秩法：p50 → 第 1 个，p90 → 第 2 个）
    assert result.overall.samples == 2
    assert result.overall.ttft_p50_ms == pytest.approx(1000)
    assert result.overall.ttft_avg_ms == pytest.approx(2000)
    assert result.overall.ttft_p90_ms == pytest.approx(3000)
    # TBT 估算：(4000-1000)/100 = 30ms，(9000-3000)/50 = 120ms
    assert result.overall.tbt_avg_ms == pytest.approx(75)
    assert result.overall.turn_avg_ms == pytest.approx(6500)


def test_latency_daily_buckets_results(lens_db):
    _seed(lens_db)

    daily = queries.latency_stats(lens_db, days=3650).daily

    assert [point.day for point in daily] == ["2026-09-24", "2026-09-25"]
    assert [point.samples for point in daily] == [1, 1]
    assert daily[1].ttft_avg_ms == pytest.approx(3000)


def test_latency_on_empty_database_returns_zeros(lens_db):
    result = queries.latency_stats(lens_db, days=30)

    assert result.overall.samples == 0
    assert result.overall.ttft_p90_ms == 0
    assert result.daily == []


def test_latency_endpoint(lens_db):
    _seed(lens_db)
    app = create_app(lens_db.execute("PRAGMA database_list").fetchone()[2])
    with TestClient(app) as client:
        body = client.get("/api/latency?days=3650").json()

    assert body["overall"]["samples"] == 2
    assert body["overall"]["tbt_avg_ms"] == pytest.approx(75)
