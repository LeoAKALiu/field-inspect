#!/usr/bin/env python3
"""Run the prepared Mac platform offline; Ctrl+C stops only this launch's children."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def durable_path(raw: str, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute() or path.resolve().is_relative_to(ROOT):
        raise ValueError(f"{label} must be an absolute persistent path outside the checkout")
    return path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=4173)
    args = parser.parse_args()
    os.umask(0o077)
    children: list[tuple[str, subprocess.Popen]] = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    with ExitStack() as stack:
        log = None

        def report(message: str, *, strict: bool = True):
            line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}"
            log_error = None
            if log is not None:
                try:
                    print(line, file=log, flush=True)
                except OSError as exc:
                    log_error = exc
            try:
                print(line, flush=True)
            except OSError:
                pass  # A closed output pipe must not interrupt service cleanup.
            if strict and log_error is not None:
                raise log_error

        def spawn(name: str, command: list[str], cwd: Path, env: dict) -> subprocess.Popen:
            output = stack.enter_context((logs / f"{name}.log").open("x", encoding="utf-8"))
            child = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            children.append((name, child))
            report(f"{name} PID={child.pid}")
            return child

        try:
            runtime = durable_path(
                os.environ.get(
                    "FIELD_RUNTIME_ROOT",
                    str(Path.home() / "Library/Application Support/FieldInspect"),
                ),
                "FIELD_RUNTIME_ROOT",
            )
            (runtime / "logs").mkdir(parents=True, exist_ok=True)
            logs = Path(
                tempfile.mkdtemp(
                    prefix=datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=runtime / "logs"
                )
            )
            log = stack.enter_context((logs / "launcher.log").open("x", encoding="utf-8"))
            report(f"Logs: {logs}")
            if sys.platform != "darwin":
                raise ValueError("This entry point requires macOS; use start-local.ps1 on Windows")
            if args.api_port == args.web_port or any(
                port < 1024 or port > 65535 for port in (args.api_port, args.web_port)
            ):
                raise ValueError("API and web ports must differ and be between 1024 and 65535")
            db = durable_path(
                os.environ.get("TWIN_DB_PATH", str(runtime / "twin.db")), "TWIN_DB_PATH"
            )
            imports = durable_path(
                os.environ.get("TWIN_IMPORT_ROOT", str(runtime / "imports")), "TWIN_IMPORT_ROOT"
            )
            env = os.environ.copy()
            env.update(
                TWIN_DB_PATH=str(db),
                TWIN_IMPORT_ROOT=str(imports),
                ASTRA_LOCAL_API_PORT=str(args.api_port),
                PYTHONUNBUFFERED="1",
            )
            if any(key.startswith("UVICORN_") and value for key, value in env.items()):
                raise ValueError(
                    "Unset UVICORN_* variables; this launcher fixes local server options"
                )
            if env.get("FIELD_SCHEMA_EXPORT") == "1":
                raise ValueError("Unset FIELD_SCHEMA_EXPORT before serving the platform")
            if env.get("FIELD_AUTH_FILE"):
                auth = Path(env["FIELD_AUTH_FILE"]).expanduser().resolve()
                if not auth.is_file():
                    raise ValueError(
                        "FIELD_AUTH_FILE does not point to an existing credential file"
                    )
                env["FIELD_AUTH_FILE"] = str(auth)
            python = ROOT / "services/api/.venv/bin/python"
            vite = ROOT / "apps/web/node_modules/vite/bin/vite.js"
            node = shutil.which("node")
            for label, path in (
                ("API Python", python),
                ("Vite", vite),
                ("web build", ROOT / "apps/web/dist/index.html"),
            ):
                if not path.is_file():
                    raise ValueError(
                        f"Missing {label}: {path}; follow docs/development/MAC_RUNTIME.md"
                    )
            if node is None:
                raise ValueError("Missing Node.js on PATH; prepare Node before offline startup")
            for port in (args.api_port, args.web_port):
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.bind(("127.0.0.1", port))
                    except OSError as exc:
                        raise ValueError(
                            f"Port {port} unavailable: {exc}; existing processes unchanged"
                        ) from exc
            db.parent.mkdir(parents=True, exist_ok=True)
            imports.mkdir(parents=True, exist_ok=True)
            # Lock both stores so differing runtime roots/ports cannot share a live API store.
            for path in (db.with_name(db.name + ".launcher.lock"), imports / ".launcher.lock"):
                lock = stack.enter_context(path.open("a"))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ValueError(
                        "Another local launcher is using this database or import directory"
                    ) from exc
            report(f"Database: {db}\nImports: {imports}")
            probe = spawn(
                "dependencies",
                [
                    str(python),
                    "-c",
                    "import bagit, cv2, fastapi, numpy, pydantic, pydantic_settings; "
                    "import tzdata, uvicorn, yaml; "
                    "from app.writer_lock import assert_single_api_process; "
                    "assert_single_api_process()",
                ],
                ROOT / "services/api",
                env,
            )
            deadline = time.monotonic() + 60
            while probe.poll() is None and not stopping and time.monotonic() < deadline:
                time.sleep(0.1)
            if stopping:
                return 0
            if probe.poll() != 0:
                raise ValueError(
                    "API dependency check failed or timed out; see dependencies.log; "
                    "prepare with uv sync --frozen --no-dev"
                )
            children.remove(("dependencies", probe))
            spawn(
                "api",
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                    "--workers",
                    "1",
                    "--no-proxy-headers",
                ],
                ROOT / "services/api",
                env,
            )
            if stopping:
                return 0
            spawn(
                "web",
                [
                    node,
                    str(vite),
                    "preview",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.web_port),
                    "--strictPort",
                ],
                ROOT / "apps/web",
                env,
            )
            (logs / "processes.json").write_text(
                json.dumps(
                    {
                        "launcher_pid": os.getpid(),
                        "api_port": args.api_port,
                        "web_port": args.web_port,
                        "database": str(db),
                        "imports": str(imports),
                        "checkout": str(ROOT),
                        "children": {name: child.pid for name, child in children},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            opener = build_opener(ProxyHandler({}))
            pending = {
                f"http://127.0.0.1:{args.api_port}/api/health",
                f"http://127.0.0.1:{args.web_port}/api/health",
                f"http://127.0.0.1:{args.web_port}/",
            }
            deadline = time.monotonic() + 60
            ready = False
            while not stopping:
                for name, child in children:
                    if child.poll() is not None:
                        raise ValueError(
                            f"{name} exited with status {child.returncode}; see {name}.log"
                        )
                for url in list(pending):
                    try:
                        with opener.open(url, timeout=1) as response:
                            is_health = url.endswith("/api/health")
                            body = json.loads(response.read(4096)) if is_health else None
                            if response.status == 200 and (
                                not is_health
                                or isinstance(body, dict)
                                and body.get("status") == "ok"
                            ):
                                pending.remove(url)
                    except (OSError, URLError, ValueError):
                        pass
                if not pending and not ready:
                    report(
                        f"Ready: http://127.0.0.1:{args.web_port} (API {args.api_port}); "
                        f"Ctrl+C to stop, or kill -TERM {os.getpid()}"
                    )
                    ready = True
                if pending and time.monotonic() >= deadline:
                    raise ValueError("Startup timed out; see api.log and web.log")
                time.sleep(0.2)
            return 0
        except (OSError, ValueError) as exc:
            report(f"ERROR: {exc}", strict=False)
            return 1
        finally:
            for _name, child in reversed(children):
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if children:
                report(
                    "Stopping this launch's services; allowing up to 30 seconds for shutdown",
                    strict=False,
                )
            deadline = time.monotonic() + 30
            for name, child in reversed(children):
                try:
                    child.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    report(
                        f"WARNING: {name} did not stop; terminating its process group; "
                        "check interrupted processing on next start",
                        strict=False,
                    )
                finally:
                    # Vite's config loader can leave an esbuild child after its parent exits.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
            if children:
                report("Stopped; database, imports and logs retained", strict=False)


if __name__ == "__main__":
    raise SystemExit(main())
