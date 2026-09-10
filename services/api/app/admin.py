"""演示数据重置 CLI。

用法：
    cd services/api && uv run python -m app.admin reset-demo [--force] [--db-path PATH]

安全约束（全部满足才执行）：
- 目标文件名必须是 twin.db；
- 解析为绝对路径后必须位于仓库目录内；
- 若文件已存在，必须是合法 SQLite 文件（文件头 "SQLite format 3\\0" 校验）；
- 127.0.0.1:8000 无后端服务监听（--force 可跳过）。

只删除该单文件：无 glob、无递归删除、无宽泛变量。重置逻辑复用 db.py/seed.py，
幂等：重复执行结果一致；文件不存在时直接重建。
"""

from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from .config import DEFAULT_DB_PATH, DEMO_DIR, REPO_ROOT
from .db import Database
from .seed import seed_if_empty

SQLITE_HEADER = b"SQLite format 3\x00"
TABLES = ("scenes", "tasks", "trajectory_points", "events", "devices", "scene_metadata")


class ResetRefused(Exception):
    """安全校验未通过，拒绝执行。"""


def _resolve_db_path(db_path: str | None = None) -> Path:
    raw = db_path or os.environ.get("TWIN_DB_PATH") or str(DEFAULT_DB_PATH)
    return Path(raw).expanduser().resolve()


def _validate_target(path: Path) -> None:
    if path.name != "twin.db":
        raise ResetRefused(f"拒绝：目标文件名必须是 twin.db（实际为 {path.name!r}）")
    if not path.is_relative_to(REPO_ROOT):
        raise ResetRefused(f"拒绝：目标路径 {path} 不在仓库目录 {REPO_ROOT} 内")
    if path.exists():
        with open(path, "rb") as f:
            header = f.read(len(SQLITE_HEADER))
        if header != SQLITE_HEADER:
            raise ResetRefused(
                f"拒绝：{path} 已存在但不是合法 SQLite 文件（文件头不匹配），不做删除"
            )


def _port_listening(host: str = "127.0.0.1", port: int = 8000, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _table_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        existing = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in TABLES:
            if table in existing:
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 — 表名为模块常量
                ).fetchone()[0]
    finally:
        conn.close()
    return counts


def _print_counts(title: str, counts: dict[str, int], out: TextIO) -> None:
    print(title, file=out)
    for table in TABLES:
        if table in counts:
            print(f"  {table}: {counts[table]}", file=out)


def reset_demo(
    db_path: str | None = None,
    *,
    force: bool = False,
    out: TextIO | None = None,
) -> int:
    """重置演示数据。返回 0 成功；返回 2 表示被安全校验拒绝。"""
    out = out if out is not None else sys.stdout
    path = _resolve_db_path(db_path)

    try:
        _validate_target(path)
    except ResetRefused as exc:
        print(exc, file=out)
        return 2

    if not force and _port_listening():
        print("拒绝：检测到 127.0.0.1:8000 有服务在监听，后端可能正在运行。", file=out)
        print("请先停止后端服务再重置；确认无风险后可用 --force 跳过该检查。", file=out)
        return 2

    print(f"目标数据库：{path}", file=out)
    if path.exists():
        _print_counts("重置前各表记录数：", _table_counts(path), out)
        os.remove(path)  # 仅删除该单文件
        print("已删除旧库文件。", file=out)
    else:
        print("库文件不存在，直接重建。", file=out)

    db = Database(str(path))
    try:
        db.init_schema()
        seeded = seed_if_empty(db, DEMO_DIR)
        counts = {
            table: db.query_one(f"SELECT COUNT(*) AS c FROM {table}")["c"] for table in TABLES
        }
    finally:
        db.close()
    _print_counts("重置后各表记录数：", counts, out)
    if seeded:
        print("演示数据已重置（种子数据已从 data/demo/ 重新载入）。", file=out)
    else:
        print("警告：库非空，未载入种子数据。", file=out)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin", description="数字孪生原型后端管理工具"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("reset-demo", help="重置演示数据（删除并重建种子库）")
    p.add_argument("--db-path", default=None, help="覆盖 TWIN_DB_PATH 指定库路径")
    p.add_argument("--force", action="store_true", help="跳过 127.0.0.1:8000 端口占用检查")
    args = parser.parse_args(argv)

    if args.command == "reset-demo":
        return reset_demo(args.db_path, force=args.force)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
