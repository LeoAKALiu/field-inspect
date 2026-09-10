"""REST 路由包。"""

from fastapi import Request

from ..db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db
