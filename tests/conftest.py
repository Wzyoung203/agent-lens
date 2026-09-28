import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_lens.storage import connect, init_db


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
