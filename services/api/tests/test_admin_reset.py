"""演示数据重置 CLI 测试（app.admin.reset_demo，直接导入调用，不 spawn 子进程）。

安全约束覆盖：非 SQLite 文件拒绝、仓库外路径拒绝、文件名非 twin.db 拒绝、
端口占用时拒绝（--force 跳过）、幂等、文件不存在时直接重建。
"""

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app import admin
from app.admin import reset_demo
from app.config import API_DIR, DEMO_DIR
from app.db import Database
from app.seed import seed_if_empty


@pytest.fixture()
def repo_db_path():
    """仓库内的临时目录（满足"位于仓库内"校验），文件名 twin.db。"""
    tmp = Path(tempfile.mkdtemp(prefix=".admin-test-", dir=API_DIR))
    yield tmp / "twin.db"
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def no_port_conflict(monkeypatch):
    """默认视为 8000 无监听，避免开发服务器影响测试。"""
    monkeypatch.setattr(admin, "_port_listening", lambda *a, **k: False)


def _make_seeded_db(path: Path) -> None:
    db = Database(str(path))
    db.init_schema()
    seed_if_empty(db, DEMO_DIR)
    db.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in admin.TABLES
        }
    finally:
        conn.close()


def _scalar(path: Path, sql: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_reset_restores_seed_data(repo_db_path):
    _make_seeded_db(repo_db_path)
    seed_counts = _counts(repo_db_path)
    assert seed_counts["events"] >= 3

    # 模拟演示过程中的数据修改：改状态 + 删一条设备
    db = Database(str(repo_db_path))
    db.execute("UPDATE events SET status = 'resolved' WHERE id = 'evt-0001'")
    db.execute("DELETE FROM devices WHERE id = 'dev-hum-001'")
    db.close()
    assert _counts(repo_db_path)["devices"] == seed_counts["devices"] - 1

    assert reset_demo(str(repo_db_path), force=True) == 0
    assert _counts(repo_db_path) == seed_counts
    assert _scalar(repo_db_path, "SELECT status FROM events WHERE id = 'evt-0001'") == "open"


def test_reset_refuses_non_sqlite_file(repo_db_path):
    repo_db_path.write_bytes(b"this is not a sqlite database")
    assert reset_demo(str(repo_db_path), force=True) == 2
    assert repo_db_path.read_bytes() == b"this is not a sqlite database"  # 未被删除


def test_reset_refuses_path_outside_repo(tmp_path):
    outside = tmp_path / "twin.db"
    _make_seeded_db(outside)
    assert reset_demo(str(outside), force=True) == 2
    assert outside.exists()  # 未被删除


def test_reset_refuses_wrong_filename(repo_db_path):
    wrong = repo_db_path.parent / "other.db"
    _make_seeded_db(wrong)
    assert reset_demo(str(wrong), force=True) == 2
    assert wrong.exists()


def test_reset_refuses_when_port_8000_busy(repo_db_path, monkeypatch):
    _make_seeded_db(repo_db_path)
    monkeypatch.setattr(admin, "_port_listening", lambda *a, **k: True)
    assert reset_demo(str(repo_db_path)) == 2  # 无 --force：拒绝
    assert reset_demo(str(repo_db_path), force=True) == 0  # --force：放行


def test_reset_rebuilds_missing_and_idempotent(repo_db_path):
    assert not repo_db_path.exists()
    assert reset_demo(str(repo_db_path), force=True) == 0  # 文件不存在：直接重建
    assert repo_db_path.exists()
    first = _counts(repo_db_path)
    assert first["scenes"] >= 1 and first["tasks"] >= 2

    assert reset_demo(str(repo_db_path), force=True) == 0  # 重复执行结果一致
    assert _counts(repo_db_path) == first
