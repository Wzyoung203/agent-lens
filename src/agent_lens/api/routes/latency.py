"""传输效率：TTFT / 轮次时长 / TBT 估算的分布（P3.1）。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import LatencyResponse

router = APIRouter(prefix="/api/latency", tags=["latency"])


@router.get("", response_model=LatencyResponse)
def get_latency(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> LatencyResponse:
    return queries.latency_stats(conn, days=days, project=project)
