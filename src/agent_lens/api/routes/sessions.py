"""会话列表、会话详情与单轮明细。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ... import queries
from ..deps import ConnDep
from ..schemas import Page, SessionDetail, SessionSummary, TurnDetail

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=Page[SessionSummary])
def list_sessions(
    conn: ConnDep,
    project: str | None = None,
    search: str | None = None,
    days: int = Query(default=30, ge=0, le=3650),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[SessionSummary]:
    return queries.list_sessions(
        conn, project=project, search=search, limit=limit, offset=offset, days=days
    )


@router.get("/{session_id}", response_model=SessionDetail)
def session_detail(session_id: str, conn: ConnDep) -> SessionDetail:
    detail = queries.session_detail(conn, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return detail


@router.get("/{session_id}/turns/{turn_id}", response_model=TurnDetail)
def turn_detail(session_id: str, turn_id: str, conn: ConnDep) -> TurnDetail:
    detail = queries.turn_detail(conn, session_id, turn_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"轮次不存在：{turn_id}")
    return detail
