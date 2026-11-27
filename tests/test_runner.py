import os
import subprocess
import sys
import time
from functools import partial

import pytest

from delegate import runner
from delegate.git import DelegateError


def test_run_ok_logs(tmp_path):
    log = tmp_path / "run.log"
    result = runner.run(
        [sys.executable, "-c", "import sys; print('hi', file=sys.stderr)"],
        tmp_path, log, timeout=10,
    )
    assert result.exit_code == 0
    assert "hi" in runner.log_tail(log)


def _child_alive(pid):
    if os.name == "nt":
        rows = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        return f'"{pid}"'.encode("ascii") in rows
    status = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True,
    ).stdout.strip()
    # A killed child may remain a zombie until the system reaps it.
    return bool(status) and not status.startswith("Z")


def test_timeout_kills_tree(tmp_path, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX", "hang")
    cmd = [runner.resolve("codex"), "exec", "-o", str(tmp_path / "worker result.txt"), "-"]
    result = runner.run(cmd, tmp_path, tmp_path / "hang.log", timeout=2)
    assert result.timed_out
    pid = int((fakes / "codex.child.pid").read_text(encoding="ascii"))
    deadline = time.monotonic() + 5
    while _child_alive(pid):
        assert time.monotonic() < deadline, f"child {pid} survived the timeout"
        time.sleep(0.05)


def test_unavailable_detected(tmp_path, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_AGY", "quota")
    cmd = [runner.resolve("agy"), "-p", "review the changes"]
    result = runner.run(cmd, tmp_path, tmp_path / "agy.log", timeout=10)
    assert result.unavailable is True


def test_parallel_runs_concurrently(tmp_path):
    cmd = [sys.executable, "-c", "import time; time.sleep(2)"]
    logs = [tmp_path / "first.log", tmp_path / "second.log"]
    jobs = [partial(runner.run, cmd, tmp_path, log, timeout=10) for log in logs]
    start = time.monotonic()
    results = runner.run_parallel(jobs)
    assert time.monotonic() - start < 3.5
    assert [result.log_path for result in results] == logs
    assert [result.exit_code for result in results] == [0, 0]


def test_stdin_and_stdout_paths(tmp_path):
    brief = tmp_path / "brief.txt"
    output = tmp_path / "output.txt"
    log = tmp_path / "run.log"
    brief.write_bytes("review ملف.py\n".encode("utf-8"))
    command = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); print('diagnostic', file=sys.stderr)"
    result = runner.run(
        [sys.executable, "-c", command], tmp_path, log, timeout=10,
        stdin_path=brief, stdout_path=output,
    )
    assert result.exit_code == 0
    assert output.read_bytes() == brief.read_bytes()
    assert runner.log_tail(log) == "diagnostic"


def test_resolve_missing():
    with pytest.raises(DelegateError, match="definitely-missing-xyz not found on PATH"):
        runner.resolve("definitely-missing-xyz")
