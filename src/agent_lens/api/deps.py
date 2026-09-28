"""FastAPI 依赖：每请求一个只读连接，请求结束关闭。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from ..storage import connect, init_db


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """打开连接并按请求关闭。

    数据库文件不存在时自动建空库：首次启动就打开页面不应该 500（设计文档 10 节）。
    """
    conn = connect(request.app.state.db_path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()


# 路由里统一用这个别名：FastAPI 要求 Depends 出现在注解里，写成默认值会被 lint 拦。
ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]
