import os
import re
import shutil
import signal
import subprocess
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .git import DelegateError


@dataclass
class RunResult:
    exit_code: int | None
    timed_out: bool
    unavailable: bool
    duration: float
    log_path: Path


def resolve(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise DelegateError(f"{name} not found on PATH")
    return path


def is_unavailable(text: str) -> bool:
    return re.search(
        r"quota|(?:rate|usage)[\s_-]*limit|\b429\b|not logged in|login required|error authenticating",
        text, re.IGNORECASE,
    ) is not None


def log_tail(path: Path, n: int = 20) -> str:
    with path.open(encoding="utf-8", errors="replace") as stream:
        return "".join(deque(stream, maxlen=n)).removesuffix("\n")


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            if process.poll() is None:
                process.kill()
                raise DelegateError(f"could not terminate process tree {process.pid}: {exc}") from exc
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # The process group already exited.


def _run(command: list[str] | str, cwd: Path, log_path: Path, timeout: int,
         stdin_path: Path | None = None, stdout_path: Path | None = None) -> RunResult:
    shell = isinstance(command, str)
    if os.name == "nt" and isinstance(command, list):
        # Use cmd.exe's outer quotes when both a batch path and its arguments contain spaces.
        shell = Path(command[0]).suffix.lower() in {".bat", ".cmd"}
    start = time.monotonic()
    try:
        with ExitStack() as stack:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = stack.enter_context(log_path.open("wb"))
            stdin = subprocess.DEVNULL if stdin_path is None else stack.enter_context(stdin_path.open("rb"))
            stdout = log
            if stdout_path is not None and stdout_path != log_path:
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                stdout = stack.enter_context(stdout_path.open("wb"))
            with subprocess.Popen(
                command, cwd=cwd, shell=shell, start_new_session=os.name != "nt",
                stdin=stdin, stdout=stdout, stderr=log,
            ) as process:
                try:
                    exit_code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill_tree(process)
                    exit_code = None
        duration = time.monotonic() - start
        with log_path.open(encoding="utf-8", errors="replace") as stream:
            unavailable = any(is_unavailable(line) for line in stream)
    except OSError as exc:
        raise DelegateError(str(exc)) from exc
    return RunResult(exit_code, exit_code is None, unavailable, duration, log_path)


def run(cmd: list[str], cwd: Path, log_path: Path, timeout: int,
        stdin_path: Path | None = None, stdout_path: Path | None = None) -> RunResult:
    # CreateProcess does not search PATHEXT for bare commands such as codex.cmd.
    if os.name == "nt" and not os.path.dirname(cmd[0]):
        cmd = [resolve(cmd[0]), *cmd[1:]]
    return _run(cmd, cwd, log_path, timeout, stdin_path, stdout_path)


def run_shell(command: str, cwd: Path, log_path: Path, timeout: int) -> RunResult:
    return _run(command, cwd, log_path, timeout)


def run_parallel(jobs: list[Callable[[], RunResult]]) -> list[RunResult]:
    with ThreadPoolExecutor() as pool:
        futures = [pool.submit(job) for job in jobs]
        return [future.result() for future in futures]
