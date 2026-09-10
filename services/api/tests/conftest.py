"""pytest 公共夹具：每个测试使用 tmp_path 下的独立临时库，短模拟推送周期。

注意：app.main 模块级 `app = create_app()` 会在 import 时按默认环境变量建库，
因此导入必须放在 monkeypatch 设置环境变量之后。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TWIN_DB_PATH", str(tmp_path / "twin-test.db"))
    monkeypatch.setenv("TWIN_IMPORT_ROOT", str(tmp_path / "imports"))
    monkeypatch.setenv("TWIN_SIM_TICK_MS", "50")
    monkeypatch.setenv("TWIN_SEED", "42")
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
