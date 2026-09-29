"""FastAPI 应用工厂：路由挂载、错误处理、静态托管。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..storage import resolve_db_path
from .routes import context, overview, projects, sessions, settings, skills, tools

API_PREFIX = "/api"


def create_app(db_path: str | Path | None = None, *, static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="agent-lens", version="0.1.0")
    app.state.db_path = resolve_db_path(db_path)
    for module in (overview, projects, sessions, tools, settings, context, skills):
        app.include_router(module.router)

    @app.exception_handler(sqlite3.Error)
    async def _sqlite_error(_request: Request, error: sqlite3.Error) -> JSONResponse:
        # 数据库层异常统一转 503：前端拿到一个明确的「后端暂时不可用」而不是 500 栈。
        return JSONResponse(status_code=503, content={"detail": f"数据库不可用：{error}"})

    if static_dir is not None and Path(static_dir).exists():
        _install_static(app, Path(static_dir))
    return app


def _install_static(app: FastAPI, static_dir: Path) -> None:
    """托管 P1.4b 的构建产物，并让非 /api/* 的未知路径回落到 index.html。

    Vue Router 用 history 模式，直接刷新 /sessions/xxx 时服务端必须返回 index.html。
    """
    assets = static_dir / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index_file = static_dir / "index.html"

    @app.get("/", include_in_schema=False)
    def _index() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{path:path}", include_in_schema=False)
    def _spa_fallback(path: str) -> FileResponse:
        if path.startswith(API_PREFIX.lstrip("/")):
            raise HTTPException(status_code=404, detail=f"未知 API 路径：/{path}")
        candidate = static_dir / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)
