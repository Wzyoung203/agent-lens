"""skill 命中排行接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import SkillsResponse

router = APIRouter(prefix="/api", tags=["skills"])


@router.get("/skills", response_model=SkillsResponse)
def skills(
    conn: ConnDep,
    days: int = Query(default=30, ge=0, le=3650),
    project: str | None = None,
) -> SkillsResponse:
    return queries.skill_stats(conn, days=days, project=project)
