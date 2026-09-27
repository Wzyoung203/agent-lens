"""SQLite 存储层：schema 初始化、幂等写入、文件水位与项目归属。

设计约定（设计文档 5.2 / 6.3 / 9.2 / 10 节）：
  * 幂等键统一为 (file_path, ordinal)，写入全部走 INSERT OR IGNORE / UPSERT
  * 单写者：本模块只给连接与写入函数，进程级互斥由调用方（P1.3 采集器）保证
  * 正文不入库：只存长度、状态与「原始文件 + ordinal」指针；
    result_summary 预留给 P1.3 脱敏后的摘要，本层不写入
  * 成本与缓存命中率是派生值，查询时由 pricing.py / 视图计算，不落库
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import ParsedSession

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
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    """统一成 UTC ISO8601 文本，保证字典序等于时间序。"""
    return value.astimezone(UTC).isoformat()


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


class WriteResult(BaseModel):
    """一次 write_parsed_session 的结果。

    inserted / skipped 按表给全零值，方便调用方直接读；UPSERT 的表（sessions、turns）
    只在 inserted 里记数，幂等性由主键保证——重复写入不会新增行。
    """

    session_id: str
    file_path: str
    inserted: dict[str, int] = Field(default_factory=dict)
    skipped: dict[str, int] = Field(default_factory=dict)


def _tally(
    cursor: sqlite3.Cursor, inserted: dict[str, int], skipped: dict[str, int], table: str
) -> None:
    if cursor.rowcount:
        inserted[table] += 1
    else:
        skipped[table] += 1


def _upsert_session(
    conn: sqlite3.Connection, parsed: ParsedSession, project: str, now_iso: str
) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, cli_version, cwd, model_provider,
            base_instructions_chars, recorded_at, project, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            cli_version = COALESCE(excluded.cli_version, sessions.cli_version),
            cwd = COALESCE(excluded.cwd, sessions.cwd),
            model_provider = COALESCE(excluded.model_provider, sessions.model_provider),
            base_instructions_chars = MAX(
                sessions.base_instructions_chars, excluded.base_instructions_chars
            ),
            recorded_at = COALESCE(sessions.recorded_at, excluded.recorded_at),
            updated_at = excluded.updated_at
        """,
        (
            parsed.session_id,
            parsed.cli_version,
            parsed.cwd,
            parsed.model_provider,
            parsed.base_instructions_chars,
            to_iso(parsed.recorded_at) if parsed.recorded_at else None,
            project,
            now_iso,
            now_iso,
        ),
    )


def _upsert_turns(conn: sqlite3.Connection, parsed: ParsedSession, now_iso: str) -> int:
    """把 turn_context 与 turn 边界合并成一行。

    同一个 turn 可能出现在同一会话的两个文件里（文件滚动），所以主键是
    (session_id, turn_id)，冲突时做字段级合并而不是覆盖。
    """
    merged: dict[str, dict[str, object]] = {}
    for context in parsed.turn_contexts:
        if not context.turn_id:
            continue
        row = merged.setdefault(context.turn_id, {})
        row["cwd"] = context.cwd
        row["model"] = context.model
        row["effort"] = context.effort
    for turn in parsed.turns:
        if not turn.turn_id:
            continue
        row = merged.setdefault(turn.turn_id, {})
        row["started_at"] = to_iso(turn.started_at) if turn.started_at else None
        row["completed_at"] = to_iso(turn.completed_at) if turn.completed_at else None
        row["duration_ms"] = turn.duration_ms
        row["aborted_reason"] = turn.aborted_reason

    for turn_id, row in merged.items():
        conn.execute(
            """
            INSERT INTO turns (
                session_id, turn_id, cwd, model, effort,
                started_at, completed_at, duration_ms, aborted_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, turn_id) DO UPDATE SET
                cwd = COALESCE(excluded.cwd, turns.cwd),
                model = COALESCE(excluded.model, turns.model),
                effort = COALESCE(excluded.effort, turns.effort),
                started_at = COALESCE(excluded.started_at, turns.started_at),
                completed_at = COALESCE(excluded.completed_at, turns.completed_at),
                duration_ms = COALESCE(excluded.duration_ms, turns.duration_ms),
                aborted_reason = COALESCE(excluded.aborted_reason, turns.aborted_reason),
                updated_at = excluded.updated_at
            """,
            (
                parsed.session_id,
                turn_id,
                row.get("cwd"),
                row.get("model"),
                row.get("effort"),
                row.get("started_at"),
                row.get("completed_at"),
                row.get("duration_ms"),
                row.get("aborted_reason"),
                now_iso,
            ),
        )
    return len(merged)


def _max_ordinal(parsed: ParsedSession) -> int:
    ordinals = [call.ordinal for call in parsed.api_calls]
    ordinals += [call.ordinal for call in parsed.tool_calls]
    ordinals += [result.ordinal for result in parsed.tool_results]
    ordinals += [item.ordinal for item in parsed.items]
    ordinals += [error.ordinal for error in parsed.parse_errors]
    return max(ordinals, default=-1)


def _touch_ingest_state(conn: sqlite3.Connection, parsed: ParsedSession, now_iso: str) -> None:
    conn.execute(
        """
        INSERT INTO ingest_state (
            file_path, session_id, cli_version, last_ordinal, parse_error_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            session_id = COALESCE(excluded.session_id, ingest_state.session_id),
            cli_version = COALESCE(excluded.cli_version, ingest_state.cli_version),
            last_ordinal = MAX(ingest_state.last_ordinal, excluded.last_ordinal),
            parse_error_count = MAX(
                ingest_state.parse_error_count, excluded.parse_error_count
            ),
            updated_at = excluded.updated_at
        """,
        (
            parsed.file_path,
            parsed.session_id,
            parsed.cli_version,
            _max_ordinal(parsed),
            len(parsed.parse_errors),
            now_iso,
        ),
    )


def write_parsed_session(
    conn: sqlite3.Connection,
    parsed: ParsedSession,
    *,
    project: str | None = None,
    now: datetime | None = None,
) -> WriteResult:
    """把一个 ParsedSession 幂等写库。

    project 为空时按 cwd 走手动映射（Task 5 落地）；命不中就是「未归类」。
    整体在一个事务里，任何一步抛错都不会留下半份数据。
    """
    now_iso = to_iso(now or utc_now())
    resolved_project = project or resolve_project(conn, parsed.cwd)
    inserted: dict[str, int] = defaultdict(int)
    skipped: dict[str, int] = defaultdict(int)

    with conn:
        _upsert_session(conn, parsed, resolved_project, now_iso)
        inserted["sessions"] += 1
        inserted["turns"] += _upsert_turns(conn, parsed, now_iso)

        for call in parsed.api_calls:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO api_calls (
                    file_path, ordinal, session_id, turn_id, response_id, timestamp,
                    input_tokens, cached_input_tokens, cache_write_input_tokens,
                    output_tokens, reasoning_output_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.file_path,
                    call.ordinal,
                    call.session_id or parsed.session_id,
                    call.turn_id,
                    call.response_id,
                    to_iso(call.timestamp) if call.timestamp else None,
                    call.usage.input_tokens,
                    call.usage.cached_input_tokens,
                    call.usage.cache_write_input_tokens,
                    call.usage.output_tokens,
                    call.usage.reasoning_output_tokens,
                    call.usage.total_tokens,
                ),
            )
            _tally(cursor, inserted, skipped, "api_calls")

        for tool_call in parsed.tool_calls:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO tool_calls (
                    file_path, ordinal, session_id, call_id, name, kind, arguments_chars
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call.file_path,
                    tool_call.ordinal,
                    parsed.session_id,
                    tool_call.call_id,
                    tool_call.name,
                    tool_call.kind,
                    len(tool_call.arguments_raw),
                ),
            )
            _tally(cursor, inserted, skipped, "tool_calls")

        for tool_result in parsed.tool_results:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO tool_results (
                    file_path, ordinal, session_id, call_id,
                    output_chars, exit_code, wall_time_seconds, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_result.file_path,
                    tool_result.ordinal,
                    parsed.session_id,
                    tool_result.call_id,
                    len(tool_result.output_text),
                    tool_result.exit_code,
                    tool_result.wall_time_seconds,
                    None if tool_result.success is None else int(tool_result.success),
                ),
            )
            _tally(cursor, inserted, skipped, "tool_results")

        for item in parsed.items:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO items (
                    file_path, ordinal, session_id, item_type, started_at_ms, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.file_path,
                    item.ordinal,
                    parsed.session_id,
                    item.item_type,
                    item.started_at_ms,
                    item.completed_at_ms,
                ),
            )
            _tally(cursor, inserted, skipped, "items")

        for index, event in enumerate(parsed.events):
            payload = event.get("payload")
            payload_keys = sorted(payload) if isinstance(payload, dict) else []
            ordinal = event.get("ordinal")
            if not isinstance(ordinal, int):
                ordinal = -1 - index
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    file_path, ordinal, session_id, event_type, payload_keys
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    parsed.file_path,
                    ordinal,
                    parsed.session_id,
                    event.get("event_type"),
                    json.dumps(payload_keys, ensure_ascii=False),
                ),
            )
            _tally(cursor, inserted, skipped, "events")

        _touch_ingest_state(conn, parsed, now_iso)
        inserted["ingest_state"] += 1

    return WriteResult(
        session_id=parsed.session_id,
        file_path=parsed.file_path,
        inserted={table: inserted.get(table, 0) for table in WRITE_TABLES},
        skipped={table: skipped.get(table, 0) for table in WRITE_TABLES},
    )


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """各表行数，用于幂等性测试与后续总览页。"""
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in COUNTS_TABLES
    }


class IngestState(BaseModel):
    """一个日志文件的读取水位。"""

    file_path: str
    session_id: str | None = None
    cli_version: str | None = None
    last_ordinal: int = -1
    byte_offset: int = 0
    file_size: int = 0
    mtime: float = 0.0
    parse_error_count: int = 0


def get_ingest_state(conn: sqlite3.Connection, file_path: str) -> IngestState | None:
    row = conn.execute("SELECT * FROM ingest_state WHERE file_path = ?", (file_path,)).fetchone()
    if row is None:
        return None
    return IngestState(
        file_path=row["file_path"],
        session_id=row["session_id"],
        cli_version=row["cli_version"],
        last_ordinal=row["last_ordinal"],
        byte_offset=row["byte_offset"],
        file_size=row["file_size"],
        mtime=row["mtime"],
        parse_error_count=row["parse_error_count"],
    )


def update_ingest_state(
    conn: sqlite3.Connection,
    *,
    file_path: str,
    session_id: str | None = None,
    cli_version: str | None = None,
    last_ordinal: int | None = None,
    byte_offset: int | None = None,
    file_size: int | None = None,
    mtime: float | None = None,
    parse_error_count: int | None = None,
    now: datetime | None = None,
) -> None:
    """更新一个文件的读取水位。未传入的参数保持原值。

    last_ordinal 只增不减（水位语义）；其余字段按传入值覆盖。
    P1.3 的增量采集器读取新内容后调用这个函数推进 offset。
    """
    now_iso = to_iso(now or utc_now())
    values = {
        "session_id": session_id,
        "cli_version": cli_version,
        "byte_offset": byte_offset,
        "file_size": file_size,
        "mtime": mtime,
        "parse_error_count": parse_error_count,
    }
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO ingest_state (file_path, updated_at) VALUES (?, ?)",
            (file_path, now_iso),
        )
        assignments = [f"{column} = ?" for column, value in values.items() if value is not None]
        params = [value for value in values.values() if value is not None]
        if last_ordinal is not None:
            assignments.append("last_ordinal = MAX(last_ordinal, ?)")
            params.append(last_ordinal)
        assignments.append("updated_at = ?")
        params.extend([now_iso, file_path])
        conn.execute(
            f"UPDATE ingest_state SET {', '.join(assignments)} WHERE file_path = ?",
            params,
        )


def assign_project(
    conn: sqlite3.Connection,
    path_prefix: str,
    project: str,
    *,
    now: datetime | None = None,
) -> None:
    """注册一条手动映射：路径前缀 -> 项目名（设计文档 9.2 节优先级最高）。"""
    now_iso = to_iso(now or utc_now())
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects (name, created_at) VALUES (?, ?)",
            (project, now_iso),
        )
        conn.execute(
            """
            INSERT INTO project_paths (path_prefix, project_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(path_prefix) DO UPDATE SET project_name = excluded.project_name
            """,
            (path_prefix, project, now_iso),
        )


def resolve_project(
    conn: sqlite3.Connection,
    cwd: str | None,
    *,
    fallback: str = UNCLASSIFIED_PROJECT,
) -> str:
    """按最长前缀匹配决定项目归属，命不中返回 fallback。

    用 substr 比较而不是 LIKE，避免 cwd 里的 % 和 _ 被当成通配符。
    v1 大小写敏感；Windows 路径归一化由 P1.3 采集器负责。
    """
    if not cwd:
        return fallback
    row = conn.execute(
        """
        SELECT project_name
        FROM project_paths
        WHERE substr(?, 1, LENGTH(path_prefix)) = path_prefix
        ORDER BY LENGTH(path_prefix) DESC
        LIMIT 1
        """,
        (cwd,),
    ).fetchone()
    return row["project_name"] if row else fallback


def refresh_session_projects(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    """按当前手动映射重算所有会话的项目归属，返回被改动的会话数。

    设置页改完映射后调用（设计文档 9.2 节的「一键指派」）。
    """
    now_iso = to_iso(now or utc_now())
    rows = conn.execute("SELECT session_id, cwd, project FROM sessions").fetchall()
    changed: list[tuple[str, str, str]] = []
    for row in rows:
        project = resolve_project(conn, row["cwd"])
        if project != row["project"]:
            changed.append((project, now_iso, row["session_id"]))
    if changed:
        with conn:
            conn.executemany(
                "UPDATE sessions SET project = ?, updated_at = ? WHERE session_id = ?",
                changed,
            )
    return len(changed)
