"""项目列表与项目详情。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import ProjectDetail, ProjectStat

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectStat])
def list_projects(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
) -> list[ProjectStat]:
    return queries.list_projects(conn, days=days)


@router.get("/{name}", response_model=ProjectDetail)
def project_detail(
    name: str,
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
) -> ProjectDetail:
    detail = queries.project_detail(conn, name, days=days)
    if detail.sessions == [] and detail.project.api_call_count == 0:
        raise HTTPException(status_code=404, detail=f"项目不存在：{name}")
    return detail
