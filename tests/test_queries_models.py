import pytest

from agent_lens import queries


def test_model_comparison_groups_by_model_and_effort(seeded_db):
    result = queries.model_comparison(seeded_db, days=3650)
    keys = {(row.model, row.effort) for row in result.rows}
    # seeded_db 的三个轮次全部是 deepseek-v4-pro / high
    assert keys == {("deepseek-v4-pro", "high")}
    (row,) = result.rows
    assert row.calls == 3
    assert row.turn_count == 3
    assert row.input_tokens == 3500
    assert row.cached_input_tokens == 1400
    assert row.output_tokens == 350
    assert row.unpriced_calls == 0
    assert row.avg_input_tokens == pytest.approx(3500 / 3)
    assert row.avg_cost_per_call == pytest.approx(row.cost / 3)


def test_model_comparison_cost_matches_hand_computed_value(seeded_db):
    result = queries.model_comparison(seeded_db, days=3650)
    # t1 空闲：(600*1.0 + 400*0.5 + 100*2.0)/1e6 = 0.001
    # t2 高峰：(1000*2.0 + 1000*1.0 + 200*4.0)/1e6 = 0.0038
    # t3 空闲：(500*1.0 + 0 + 50*2.0)/1e6 = 0.0006
    assert result.total_cost == pytest.approx(0.0054)
    assert result.rows[0].cost == pytest.approx(0.0054)
    assert result.currency == "USD"


def test_model_comparison_cache_hit_rate(seeded_db):
    (row,) = queries.model_comparison(seeded_db, days=3650).rows
    assert row.cache_hit_rate == pytest.approx(1400 / 3500)


def test_model_comparison_filters_by_project(seeded_db):
    alpha = queries.model_comparison(seeded_db, days=3650, project="alpha")
    assert alpha.rows[0].calls == 2
    assert alpha.total_cost == pytest.approx(0.0048)


def test_models_endpoint_returns_rows(seeded_db):
    from fastapi.testclient import TestClient

    from agent_lens.api.app import create_app

    db_file = seeded_db.execute("PRAGMA database_list").fetchone()[2]
    with TestClient(create_app(db_file)) as client:
        response = client.get("/api/models?days=3650")
    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["model"] == "deepseek-v4-pro"
    assert body["total_cost"] == pytest.approx(0.0054)
