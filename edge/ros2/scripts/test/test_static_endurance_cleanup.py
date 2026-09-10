"""Exercise actual shell cleanup against an orphanable process group, without ROS."""
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Jetson /proc process groups")


def live_members(pgid):
    members = []
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                members.append(int(path.parent.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return members


@pytest.mark.parametrize("leader_already_exited", [False, True])
def test_cleanup_stops_owned_descendants_and_preserves_other_group(leader_already_exited):
    script = Path(__file__).resolve().parents[1] / "run_static_endurance.sh"
    source = script.read_text()
    cleanup = re.search(r"cleanup\(\) \{.*?\n\}", source, re.S).group(0)
    child = (
        "import os,signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "print(os.getpid(), flush=True); time.sleep(60)"
    )
    parent = (
        "import signal,subprocess,sys,time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        f"p=subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.PIPE); "
        "print(p.stdout.readline().decode().strip(), flush=True); time.sleep(60)"
    )
    launch = subprocess.Popen(
        [sys.executable, "-c", parent], start_new_session=True,
        stdout=subprocess.PIPE, text=True,
    )
    unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        assert select.select([launch.stdout], [], [], 5)[0], "fixture did not become ready"
        child_pid = int(launch.stdout.readline())
        assert os.getpgid(child_pid) == launch.pid
        if leader_already_exited:
            launch.terminate()
            launch.wait(timeout=2)
        # Keep real signals and the production cleanup body; shorten only its wait loop.
        harness = (
            "set -euo pipefail\nrecording_started=false\n"
            "declare -a monitor_pids=()\n"
            f"launch_pid={launch.pid}\n"
            "seq() { printf '1\\n'; }\nsleep() { command sleep 0.1; }\n"
            + cleanup + "\ncleanup\n"
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        launch.wait(timeout=2)
        assert not live_members(launch.pid), "owned child survived launcher cleanup"
        assert unrelated.poll() is None, "cleanup affected an unrelated process group"
    finally:
        for process in (launch, unrelated):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def test_cleanup_sends_only_one_interrupt_to_launch_children(tmp_path):
    """ROS launch forwards SIGINT; the caller must not also signal its children."""
    script = Path(__file__).resolve().parents[1] / "run_static_endurance.sh"
    cleanup = re.search(r"cleanup\(\) \{.*?\n\}", script.read_text(), re.S).group(0)
    count_file = tmp_path / "interrupts.txt"
    child = """
import signal, sys, time
from pathlib import Path
count = 0
deadline = None
def stop(signum, frame):
    global count, deadline
    count += 1
    Path(sys.argv[1]).write_text(str(count))
    if deadline is None:
        deadline = time.monotonic() + 0.5
signal.signal(signal.SIGINT, stop)
print('ready', flush=True)
while deadline is None or time.monotonic() < deadline:
    time.sleep(0.01)
"""
    parent = """
import signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], stdout=subprocess.PIPE)
def stop(signum, frame):
    time.sleep(0.1)  # Separate a group SIGINT from launch's forwarded SIGINT.
    child.send_signal(signal.SIGINT)
    child.wait(timeout=3)
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
print(child.stdout.readline().decode().strip(), flush=True)
while True:
    time.sleep(0.01)
"""
    launch = subprocess.Popen(
        [sys.executable, "-c", parent, child, str(count_file)],
        start_new_session=True, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([launch.stdout], [], [], 5)[0], "fixture did not become ready"
        assert launch.stdout.readline().strip() == "ready"
        harness = (
            "set -euo pipefail\nrecording_started=false\ndeclare -a monitor_pids=()\n"
            f"launch_pid={launch.pid}\n"
            "seq() { command seq 1 15; }\nsleep() { command sleep 0.1; }\n"
            + cleanup + "\ncleanup\n"
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=6)
        assert result.returncode == 0, result.stderr
        launch.wait(timeout=2)
        assert count_file.read_text() == "1", "launch child received duplicate SIGINT"
        assert not live_members(launch.pid)
    finally:
        try:
            os.killpg(launch.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        launch.wait(timeout=2)
