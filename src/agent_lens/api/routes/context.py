"""上下文成本页的聚合接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import ContextOverviewResponse

router = APIRouter(prefix="/api", tags=["context"])


@router.get("/context", response_model=ContextOverviewResponse)
def context(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> ContextOverviewResponse:
    return queries.context_overview(conn, days=days, project=project)
