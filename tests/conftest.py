import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_lens.models import (
    ApiCallRecord,
    ParsedSession,
    TokenUsage,
    ToolCallRecord,
    ToolResultRecord,
    TurnContextRecord,
    TurnRecord,
)
from agent_lens.pricing import PriceEntry, upsert_price
from agent_lens.storage import assign_project, connect, init_db, write_parsed_session

SEEDED_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class MutableNow:
    """可控时钟：测试退避与限速时替代 datetime.now。"""

    def __init__(self, value: datetime | None = None):
        self.value = value or datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value = self.value + timedelta(seconds=seconds)


@pytest.fixture
def write_jsonl(tmp_path: Path):
    """把若干行对象写成一个 JSONL 文件，返回路径。"""

    def _write(
        rows: list[dict | str],
        name: str = "rollout-2026-09-24T23-00-48-01a0d3ee-f6d2-7af3-80e0-641744b01963.jsonl",
    ) -> Path:
        target = tmp_path / name
        with target.open("w", encoding="utf-8") as handle:
            for row in rows:
                if isinstance(row, str):
                    handle.write(row + "\n")
                else:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return target

    return _write


@pytest.fixture
def lens_db(tmp_path: Path):
    """建一个初始化好的临时数据库连接。"""
    conn = connect(tmp_path / "lens.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sessions_dir(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    return directory


def _seed_turn(
    file_path: str,
    session_id: str,
    turn_id: str,
    *,
    started_at: datetime,
    api_ordinal: int,
    tool_ordinal: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    tool_name: str,
    tool_failed: bool,
) -> ParsedSession:
    return ParsedSession(
        session_id=session_id,
        file_path=file_path,
        turn_contexts=[TurnContextRecord(turn_id=turn_id, model="deepseek-v4-pro", effort="high")],
        turns=[
            TurnRecord(
                turn_id=turn_id,
                started_at=started_at,
                completed_at=started_at + timedelta(seconds=30),
                duration_ms=30_000,
            )
        ],
        api_calls=[
            ApiCallRecord(
                file_path=file_path,
                ordinal=api_ordinal,
                session_id=session_id,
                turn_id=turn_id,
                response_id=f"r-{turn_id}",
                usage=TokenUsage(
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                ),
            )
        ],
        tool_calls=[
            ToolCallRecord(
                file_path=file_path,
                ordinal=tool_ordinal,
                call_id=f"call-{turn_id}",
                name=tool_name,
                arguments_raw="{}",
            )
        ],
        tool_results=[
            ToolResultRecord(
                file_path=file_path,
                ordinal=tool_ordinal + 1,
                call_id=f"call-{turn_id}",
                output_text="Exit code: 1\nWall time: 2 seconds",
                exit_code=1 if tool_failed else 0,
                wall_time_seconds=2.0,
                success=not tool_failed,
                result_summary="Exit code: 1",
            )
        ],
    )


@pytest.fixture
def seeded_db(tmp_path):
    """一个小而完整的库：两个项目、三个轮次、两根失败/成功工具链、四档价格。

    价格故意做成整数倍，方便在断言里直接验算：
      idle: input 1.0, cached 0.5, output 2.0 (per Mtok)
      peak: input 2.0, cached 1.0, output 4.0
    """
    conn = connect(tmp_path / "seeded.db")
    init_db(conn)
    assign_project(conn, "/work/alpha", "alpha")
    assign_project(conn, "/work/beta", "beta")
    for window, scale in (("idle", 1.0), ("peak", 2.0)):
        upsert_price(
            conn,
            PriceEntry(
                provider="deepseek",
                model="deepseek-v4-pro",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                time_window=window,
                input_price_per_mtok=1.0 * scale,
                cached_input_price_per_mtok=0.5 * scale,
                output_price_per_mtok=2.0 * scale,
            ),
        )

    alpha_file = "/sessions/rollout-alpha.jsonl"
    conn.execute(
        "INSERT OR IGNORE INTO sessions (session_id, cwd, model_provider, project, "
        "first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "/work/alpha/app", "deepseek", "alpha", "2026-09-20T00:00:00+00:00",
         "2026-09-20T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sessions (session_id, cwd, model_provider, project, "
        "first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("s2", "/work/beta/app", "deepseek", "beta", "2026-09-22T00:00:00+00:00",
         "2026-09-22T00:00:00+00:00"),
    )
    conn.commit()

    # alpha：t1 周一 10:00 UTC（空闲），t2 周二 02:00 UTC（高峰）；t3 周三 12:00（空闲）。
    # 日期必须落在工作日：deepseek 的高峰时段只在周一至周五生效。
    write_parsed_session(
        conn,
        _seed_turn(
            alpha_file, "s1", "t1",
            started_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
            api_ordinal=5, tool_ordinal=3,
            input_tokens=1000, cached_input_tokens=400, output_tokens=100,
            tool_name="exec_command", tool_failed=True,
        ),
    )
    write_parsed_session(
        conn,
        _seed_turn(
            alpha_file, "s1", "t2",
            started_at=datetime(2026, 9, 22, 2, 0, tzinfo=UTC),
            api_ordinal=12, tool_ordinal=9,
            input_tokens=2000, cached_input_tokens=1000, output_tokens=200,
            tool_name="apply_patch", tool_failed=False,
        ),
    )
    write_parsed_session(
        conn,
        _seed_turn(
            "/sessions/rollout-beta.jsonl", "s2", "t3",
            started_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            api_ordinal=3, tool_ordinal=2,
            input_tokens=500, cached_input_tokens=0, output_tokens=50,
            tool_name="exec_command", tool_failed=False,
        ),
    )
    yield conn
    conn.close()
