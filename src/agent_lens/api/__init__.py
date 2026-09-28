"""查询 API：薄薄一层 FastAPI 路由，聚合逻辑都在 queries.py。"""

from .app import create_app

__all__ = ["create_app"]
