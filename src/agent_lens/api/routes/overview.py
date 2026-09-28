"""存活探针与总览接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ...storage import SCHEMA_VERSION
from ..deps import ConnDep
from ..schemas import HealthResponse, OverviewResponse

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", schema_version=SCHEMA_VERSION)


@router.get("/overview", response_model=OverviewResponse)
def overview(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
    provider: str | None = None,
) -> OverviewResponse:
    return queries.overview(conn, days=days, project=project, provider=provider)
