"""运行配置：全部通过环境变量覆盖，离线可运行。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]  # services/api
REPO_ROOT = Path(__file__).resolve().parents[3]  # 仓库根目录
DEMO_DIR = REPO_ROOT / "data" / "demo"
DEFAULT_DATA_ROOT = REPO_ROOT / "data" / "var"
DEFAULT_DB_PATH = DEFAULT_DATA_ROOT / "twin.db"
DEFAULT_IMPORT_ROOT = REPO_ROOT / "data" / "imports"

_CODE_RELEASE_DIRS = ("services", "apps", "packages", "docker")


class DurablePathError(ValueError):
    """Database or import directories were placed inside a code-release tree."""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_inside_code_release(path: Path, repo_root: Path = REPO_ROOT) -> bool:
    resolved = path.resolve()
    return any(_is_relative_to(resolved, repo_root / name) for name in _CODE_RELEASE_DIRS)


def assert_durable_paths(settings: Settings, repo_root: Path = REPO_ROOT) -> None:
    """Refuse DB/import paths that would vanish on a code-release swap."""
    checks = (
        ("TWIN_DB_PATH", settings.db_path),
        ("TWIN_IMPORT_ROOT", settings.import_root),
    )
    for label, raw in checks:
        if raw == ":memory:":
            continue
        path = Path(raw)
        if is_inside_code_release(path, repo_root):
            raise DurablePathError(
                f"{label}={path} is inside a code-release directory; "
                "durable run data must live outside services/, apps/, packages/, and docker/"
            )


@dataclass(frozen=True)
class Settings:
    db_path: str
    import_root: str
    sim_tick_ms: int
    seed: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=os.environ.get("TWIN_DB_PATH", str(DEFAULT_DB_PATH)),
            import_root=os.environ.get("TWIN_IMPORT_ROOT", str(DEFAULT_IMPORT_ROOT)),
            sim_tick_ms=int(os.environ.get("TWIN_SIM_TICK_MS", "1000")),
            seed=int(os.environ.get("TWIN_SEED", "42")),
        )
