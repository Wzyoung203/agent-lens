"""SQLite 存储层：schema 初始化、幂等写入、文件水位与项目归属。

设计约定（设计文档 5.2 / 6.3 / 9.2 / 10 节）：
  * 幂等键统一为 (file_path, ordinal)，写入全部走 INSERT OR IGNORE / UPSERT
  * 单写者：本模块只给连接与写入函数，进程级互斥由调用方（P1.3 采集器）保证
  * 正文不入库：只存长度、状态与「原始文件 + ordinal」指针；
    result_summary 预留给 P1.3 脱敏后的摘要，本层不写入
  * 成本与缓存命中率是派生值，查询时由 pricing.py / 视图计算，不落库
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path.home() / ".agent-lens" / "agent-lens.db"
DB_PATH_ENV = "AGENT_LENS_DB"
UNCLASSIFIED_PROJECT = "未归类"
COUNTS_TABLES = (
    "sessions",
    "ingest_state",
    "turns",
    "api_calls",
    "tool_calls",
    "tool_results",
    "items",
    "events",
    "projects",
    "project_paths",
    "pricing",
)
WRITE_TABLES = (
    "sessions",
    "turns",
    "api_calls",
    "tool_calls",
    "tool_results",
    "items",
    "events",
    "ingest_state",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """统一成 UTC ISO8601 文本，保证字典序等于时间序。"""
    return value.astimezone(timezone.utc).isoformat()


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """按 参数 > 环境变量 > 默认路径 的顺序决定数据库位置。"""
    if db_path is not None:
        return Path(db_path).expanduser()
    from_env = os.environ.get(DB_PATH_ENV)
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_DB_PATH


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """打开数据库并把连接调成分析场景需要的状态。"""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表建视图并写入 schema 版本号，可重复执行。"""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本 {current} 高于本代码支持的 {SCHEMA_VERSION}，请升级 agent-lens"
        )
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
