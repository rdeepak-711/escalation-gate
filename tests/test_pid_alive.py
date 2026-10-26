"""pid_alive with real processes. The MCP server starts each sweep as its own child and never waits on it, so a
sweep that died leaves a zombie behind; it must read as dead, or it blocks every new sweep with "already running"."""
import os
import signal
import subprocess
import sys
import time

import gate.mcp_server as ms


def test_a_running_child_is_alive():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert ms.pid_alive(proc.pid)
    finally:
        proc.kill()
        proc.wait()


def test_a_child_that_exited_is_dead_before_anyone_waits_on_it():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # kept referenced, so nothing else reaps it
    deadline = time.monotonic() + 10
    while ms.pid_alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not ms.pid_alive(proc.pid)
    assert proc.pid  # still referenced here


def test_a_process_that_is_not_our_child_is_alive_while_it_runs():
    out = subprocess.run(["sh", "-c", "sleep 30 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True)
    pid = int(out.stdout.strip())
    try:
        assert ms.pid_alive(pid)
    finally:
        os.kill(pid, signal.SIGKILL)


def test_no_pid_is_dead():
    assert not ms.pid_alive(None)
    assert not ms.pid_alive(0)


def test_a_reaped_child_is_dead():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not ms.pid_alive(proc.pid)
