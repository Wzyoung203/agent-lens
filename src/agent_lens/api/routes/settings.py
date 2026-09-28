"""设置页接口：价目表、项目映射、状态与重算。

这是整套 API 里唯一的写路径（设计文档 5.1 节：API「管理配置」，不写业务数据）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import storage
from ...config import load_config
from ...pricing import PriceEntry, TimeWindow, list_prices, upsert_price
from ..deps import ConnDep
from ..schemas import (
    AppStatus,
    DeletedResponse,
    ProjectMapping,
    ProjectMappingRequest,
    ReassignResponse,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/pricing", response_model=list[PriceEntry])
def get_pricing(
    conn: ConnDep,
    provider: str | None = None,
    model: str | None = None,
) -> list[PriceEntry]:
    return list_prices(conn, provider=provider, model=model)


@router.put("/pricing", response_model=PriceEntry)
def put_pricing(entry: PriceEntry, conn: ConnDep) -> PriceEntry:
    upsert_price(conn, entry)
    return entry


@router.delete("/pricing", response_model=DeletedResponse)
def delete_pricing(
    conn: ConnDep,
    provider: str,
    model: str,
    effective_from: str,
    time_window: TimeWindow = "any",
) -> DeletedResponse:
    with conn:
        cursor = conn.execute(
            "DELETE FROM pricing WHERE provider = ? AND model = ? "
            "AND effective_from = ? AND time_window = ?",
            (provider, model, effective_from, time_window),
        )
    return DeletedResponse(deleted=bool(cursor.rowcount))


@router.get("/projects", response_model=list[ProjectMapping])
def get_project_mappings(conn: ConnDep) -> list[ProjectMapping]:
    rows = conn.execute(
        """
        SELECT p.name AS project, pp.path_prefix AS path_prefix
        FROM projects p
        LEFT JOIN project_paths pp ON pp.project_name = p.name
        ORDER BY p.name, pp.path_prefix
        """
    ).fetchall()
    counts = {
        row["project"]: int(row["n"])
        for row in conn.execute(
            "SELECT project, COUNT(*) AS n FROM sessions GROUP BY project"
        ).fetchall()
    }
    grouped: dict[str, ProjectMapping] = {}
    for row in rows:
        mapping = grouped.setdefault(
            row["project"],
            ProjectMapping(
                project=row["project"], session_count=counts.get(row["project"], 0)
            ),
        )
        if row["path_prefix"]:
            mapping.prefixes.append(row["path_prefix"])
    for project, count in counts.items():
        grouped.setdefault(
            project, ProjectMapping(project=project, session_count=count)
        )
    return [grouped[name] for name in sorted(grouped)]


@router.put("/projects", response_model=ProjectMapping)
def put_project_mapping(body: ProjectMappingRequest, conn: ConnDep) -> ProjectMapping:
    storage.assign_project(conn, body.path_prefix, body.project)
    storage.refresh_session_projects(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE project = ?", (body.project,)
    ).fetchone()[0]
    return ProjectMapping(
        project=body.project, prefixes=[body.path_prefix], session_count=int(count)
    )


@router.delete("/projects", response_model=ReassignResponse)
def delete_project_mapping(
    conn: ConnDep, path_prefix: str = Query(...)
) -> ReassignResponse:
    with conn:
        cursor = conn.execute(
            "DELETE FROM project_paths WHERE path_prefix = ?", (path_prefix,)
        )
    reassigned = storage.refresh_session_projects(conn)
    return ReassignResponse(
        deleted=bool(cursor.rowcount), sessions_reassigned=reassigned
    )


@router.post("/projects/refresh", response_model=ReassignResponse)
def refresh_projects(conn: ConnDep) -> ReassignResponse:
    return ReassignResponse(sessions_reassigned=storage.refresh_session_projects(conn))


@router.get("/status", response_model=AppStatus)
def status(conn: ConnDep) -> AppStatus:
    config = load_config()
    queue_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM report_queue GROUP BY status"
    ).fetchall()
    queue = {"pending": 0, "sent": 0, "failed": 0}
    for row in queue_rows:
        queue[row["status"]] = int(row["n"])
    parse_errors = conn.execute(
        "SELECT COALESCE(SUM(parse_error_count), 0) FROM ingest_state"
    ).fetchone()[0]
    return AppStatus(
        db_path=str(config.db_path),
        schema_version=storage.SCHEMA_VERSION,
        counts=storage.counts(conn),
        parse_errors=int(parse_errors),
        report_queue=queue,
        langfuse_enabled=bool(config.langfuse.enabled),
        sessions_dir=str(config.sessions_dir),
    )
