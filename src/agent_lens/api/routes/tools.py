"""工具调用分布、耗时与失败。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import Page, ToolFailure, ToolsResponse

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=ToolsResponse)
def tools(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> ToolsResponse:
    return queries.tool_stats(conn, days=days, project=project)


@router.get("/failures", response_model=Page[ToolFailure])
def failures(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[ToolFailure]:
    return queries.tool_failures(
        conn, days=days, project=project, limit=limit, offset=offset
    )
