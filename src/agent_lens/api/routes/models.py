"""模型与推理强度成本对比接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import ModelComparisonResponse

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", response_model=ModelComparisonResponse)
def models(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> ModelComparisonResponse:
    return queries.model_comparison(conn, days=days, project=project)
