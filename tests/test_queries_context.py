import pytest

from agent_lens import queries, storage
from agent_lens.api.app import create_app
from agent_lens.context import BLOCK_UNATTRIBUTED, BLOCKS, BlockChars, CallBreakdown

ALPHA_FILE = "/sessions/rollout-alpha.jsonl"


def _breakdown(ordinal: int, input_tokens: int, turn_id: str) -> CallBreakdown:
    return CallBreakdown(
        file_path=ALPHA_FILE,
        ordinal=ordinal,
        session_id="s1",
        turn_id=turn_id,
        input_tokens=input_tokens,
        blocks={name: BlockChars(other=100) for name in BLOCKS},
        unattributed_tokens=float(input_tokens) - 120.0,
    )


@pytest.fixture
def context_db(seeded_db):
    """在 seeded_db 的基础上，为 alpha 的两次调用补上上下文分解。"""
    storage.write_context_breakdown(seeded_db, [_breakdown(5, 1000, "t1"), _breakdown(12, 2000, "t2")])
    return seeded_db


def test_context_overview_sums_blocks(context_db):
    result = queries.context_overview(context_db, days=3650)
    by_block = {stat.block: stat for stat in result.blocks}
    # input 是窗口内全部调用的真实分母（alpha 1000+2000，beta 500）
    assert result.input_tokens == 3500
    assert result.analyzed_calls == 2
    assert by_block[BLOCK_UNATTRIBUTED].tokens == pytest.approx(2760.0)
    assert by_block[BLOCK_UNATTRIBUTED].share == pytest.approx(2760.0 / 3000)
    # 已分析的两次调用，五块之和等于它们的真实 input（3000），不等于窗口总量的 3500
    assert sum(stat.tokens for stat in result.blocks) == pytest.approx(3000.0)
    assert by_block["history"].other_chars == 200


def test_context_overview_reports_coverage(context_db):
    result = queries.context_overview(context_db, days=3650)
    # seeded_db 里三个调用（alpha 两次 + beta 一次），分解只覆盖了 alpha 的两次
    assert result.total_calls == 3
    assert result.coverage == pytest.approx(2 / 3)


def test_context_overview_filters_by_project(context_db):
    beta_only = queries.context_overview(context_db, days=3650, project="beta")
    assert beta_only.analyzed_calls == 0
    assert beta_only.input_tokens == 500


def test_context_endpoint_returns_blocks(context_db):
    db_file = context_db.execute("PRAGMA database_list").fetchone()[2]
    from fastapi.testclient import TestClient

    with TestClient(create_app(db_file)) as client:
        response = client.get("/api/context?days=3650")
    assert response.status_code == 200
    body = response.json()
    blocks = {row["block"]: row for row in body["blocks"]}
    assert blocks[BLOCK_UNATTRIBUTED]["tokens"] == pytest.approx(2760.0)
    assert len(blocks) == 5
