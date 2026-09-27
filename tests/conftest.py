import json
from pathlib import Path

import pytest

from agent_lens.storage import connect, init_db


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
