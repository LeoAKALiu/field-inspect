"""Mac launcher boundary checks, using temporary stores and the prepared real services."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "scripts/start-local.py"
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac startup entry point")
HTTP = build_opener(ProxyHandler({}))


@pytest.fixture()
def runtime(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TWIN_", "FIELD_", "ASTRA_", "UVICORN_"))
        and key != "WEB_CONCURRENCY"
    }
    env["FIELD_RUNTIME_ROOT"] = str(tmp_path / "persistent")
    env["ASTRA_API_TOKEN"] = "synthetic-launcher-test-only"
    with socket.socket() as api, socket.socket() as web:
        api.bind(("127.0.0.1", 0))
        web.bind(("127.0.0.1", 0))
        ports = (api.getsockname()[1], web.getsockname()[1])
    return env, ports


def command(ports, launcher=LAUNCHER):
    return [sys.executable, str(launcher), "--api-port", str(ports[0]), "--web-port", str(ports[1])]


def request(port, path, env, *, method="GET", authenticated=True):
    headers = {"Authorization": f"Bearer {env['ASTRA_API_TOKEN']}"} if authenticated else {}
    with HTTP.open(
        Request(f"http://127.0.0.1:{port}{path}", headers=headers, method=method), timeout=2
    ) as response:
        return response.read()


def assert_ports_released(ports):
    for port in ports:
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0


@contextmanager
def running(env, ports, stop_signal=signal.SIGINT):
    process = subprocess.Popen(
        command(ports), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(process.communicate()[0])
            try:
                request(ports[1], "/api/health", env)
                request(ports[1], "/", env)
                break
            except (URLError, OSError):
                time.sleep(0.1)
        else:
            pytest.fail("Real startup entry point did not become reachable")
        yield process
    finally:
        process.send_signal(stop_signal)
        output, _ = process.communicate(timeout=40)
        assert process.returncode == 0, output
        assert_ports_released(ports)


def test_stop_restart_keeps_data_and_access_protection(runtime):
    env, ports = runtime
    with running(env, ports):
        with pytest.raises(HTTPError) as denied:
            request(ports[1], "/api/access/me", env, authenticated=False)
        assert denied.value.code == 401
        request(ports[1], "/api/access/login", env, method="POST")
        audit = json.loads(request(ports[1], "/api/access/audit", env))
        assert any(item["path"] == "/api/access/login" for item in audit["items"])
        original = Path(env["FIELD_RUNTIME_ROOT"]) / "imports/inbox/synthetic-sentinel.txt"
        original.write_text("synthetic offline persistence check", encoding="utf-8")
    with running(env, ports, signal.SIGTERM):
        assert json.loads(request(ports[1], "/api/access/audit", env)) == audit
        assert original.read_text(encoding="utf-8") == "synthetic offline persistence check"
    assert len(list(Path(env["FIELD_RUNTIME_ROOT"]).glob("logs/*/launcher.log"))) == 2


@pytest.mark.parametrize("missing", ["API Python", "web build"])
def test_missing_prepared_files_are_reported_without_starting(tmp_path, runtime, missing):
    env, ports = runtime
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copyfile(LAUNCHER, checkout / "scripts/start-local.py")
    if missing == "web build":
        (checkout / "services/api").mkdir(parents=True)
        (checkout / "apps/web").mkdir(parents=True)
        (checkout / "services/api/.venv").symlink_to(ROOT / "services/api/.venv")
        (checkout / "apps/web/node_modules").symlink_to(ROOT / "apps/web/node_modules")
    result = subprocess.run(
        command(ports, checkout / "scripts/start-local.py"),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert f"Missing {missing}" in result.stdout
    logs = list(Path(env["FIELD_RUNTIME_ROOT"]).glob("logs/*/launcher.log"))
    assert len(logs) == 1 and f"Missing {missing}" in logs[0].read_text()
    assert not (Path(env["FIELD_RUNTIME_ROOT"]) / "twin.db").exists()
    assert_ports_released(ports)


def test_port_conflict_leaves_existing_listener_intact(runtime):
    env, ports = runtime
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", ports[1]))
        existing.listen()
        result = subprocess.run(command(ports), env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 1
        assert f"Port {ports[1]} unavailable" in result.stdout
        with socket.create_connection(("127.0.0.1", ports[1]), timeout=1):
            accepted, _ = existing.accept()
            accepted.close()
    assert_ports_released(ports)


def test_api_startup_failure_stops_web_and_keeps_failure_log(runtime):
    env, ports = runtime
    env["TWIN_SIM_TICK_MS"] = "invalid-test-value"
    result = subprocess.run(command(ports), env=env, capture_output=True, text=True, timeout=100)
    assert result.returncode == 1
    assert "api exited" in result.stdout
    api_logs = list(Path(env["FIELD_RUNTIME_ROOT"]).glob("logs/*/api.log"))
    assert len(api_logs) == 1 and "invalid-test-value" in api_logs[0].read_text()
    assert_ports_released(ports)


@pytest.mark.parametrize("variable", ["UVICORN_FD", "UVICORN_RELOAD"])
def test_implicit_server_overrides_are_rejected(runtime, variable):
    env, ports = runtime
    env[variable] = "1"
    result = subprocess.run(command(ports), env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "Unset UVICORN_*" in result.stdout
    assert not (Path(env["FIELD_RUNTIME_ROOT"]) / "twin.db").exists()
    assert_ports_released(ports)
