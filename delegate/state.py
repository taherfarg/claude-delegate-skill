import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import git, runner
from .config import Config, match_glob
from .git import DelegateError


@dataclass
class Task:
    id: str
    title: str
    created: str
    base_commit: str
    scope: list[str]
    dirty: dict[str, str]
    worktree: str | None = None
    contributors: list[str] = field(default_factory=list)
    gates: dict = field(default_factory=dict)
    reviews: list[dict] = field(default_factory=list)
    triage: dict = field(default_factory=dict)
    approval: dict | None = None
    outcome: str | None = None
    events: list[dict] = field(default_factory=list)


def task_dir(repo: Path, task_id: str) -> Path:
    if task_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", task_id):
        raise DelegateError(f"invalid task id: {task_id!r}")
    return git.main_root(repo) / ".delegate" / "tasks" / task_id


def task_tree(repo: Path, task: Task) -> Path:
    return Path(task.worktree) if task.worktree else git.main_root(repo)


def load_task(repo: Path, task_id: str) -> Task:
    path = task_dir(repo, task_id) / "state.json"
    try:
        return Task(**json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise DelegateError(f"task {task_id} does not exist") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise DelegateError(f"cannot load task {task_id}: {exc}") from exc


def save_task(repo: Path, task: Task) -> None:
    directory = task_dir(repo, task.id)
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(task), stream, indent=2)
            stream.write("\n")
        os.replace(temporary, directory / "state.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock_guard(directory: Path) -> Iterator[None]:
    # Keep one inode for the short create/remove operations, even across stale lock replacement.
    with (directory / ".guard").open("a+b") as guard:
        if os.name == "nt":
            import msvcrt

            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(guard, fcntl.LOCK_EX)
        yield


@contextmanager
def task_lock(repo: Path, task_id: str) -> Iterator[None]:
    directory = task_dir(repo, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".lock"
    owner = uuid4().hex.encode("ascii")
    with _lock_guard(directory):
        if lock.exists():
            if time.time() - lock.stat().st_mtime <= 2 * 60 * 60:
                raise DelegateError(f"task {task_id} is busy")
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(owner)
    try:
        yield
    finally:
        # A command that outlives its lease must leave its successor's lock alone.
        with _lock_guard(directory):
            if lock.exists() and lock.read_bytes() == owner:
                lock.unlink()


def _exclude_state(root: Path) -> None:
    path = root / ".git" / "info" / "exclude"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        existing = stream.read()
        if b".delegate/" not in existing.splitlines():
            prefix = b"\n" if existing and not existing.endswith(b"\n") else b""
            stream.write(prefix + b".delegate/\n")


def create_task(repo: Path, task_id: str, title: str, scope: list[str], cfg: Config,
                worktree: bool = False) -> Task:
    directory = task_dir(repo, task_id)
    root = git.main_root(repo)
    base = git.head(root)
    _exclude_state(root)
    with task_lock(root, task_id):
        if (directory / "state.json").exists():
            raise DelegateError(f"task {task_id} already exists")
        tree = root
        if worktree:
            tree = root.with_name(f"{root.name}-wt-{task_id}")
            git._run(root, "worktree", "add", "-b", f"delegate/{task_id}", str(tree), base)
        task = Task(
            id=task_id, title=title, created=datetime.now(timezone.utc).isoformat(),
            base_commit=base, scope=list(scope),
            dirty={rel: git.file_sha(tree, rel) for rel in git.status_files(tree)},
            worktree=str(tree) if worktree else None,
        )
        save_task(root, task)
        if worktree and cfg.worktree_setup:
            result = runner.run_shell(cfg.worktree_setup, tree, directory / "setup.log", cfg.timeouts.gate)
            if result.timed_out or result.exit_code != 0:
                reason = "timed out" if result.timed_out else f"failed with exit code {result.exit_code}"
                tail = runner.log_tail(result.log_path)
                raise DelegateError(f"worktree setup {reason}\n{tail}".rstrip())
        return task


def _changes(task: Task, tree: Path) -> tuple[list[str], list[str]]:
    tracked, untracked = git._changes_since(tree, task.base_commit)
    files = sorted(
        rel for rel in tracked | untracked
        if rel not in task.dirty or git.file_sha(tree, rel) != task.dirty[rel]
    )
    return files, sorted(untracked.intersection(files))


def changed_files(task: Task, tree: Path) -> list[str]:
    return _changes(task, tree)[0]


def touched_dirty(task: Task, tree: Path) -> list[str]:
    return sorted(rel for rel, sha in task.dirty.items() if git.file_sha(tree, rel) != sha)


def out_of_scope(task: Task, files: list[str]) -> list[str]:
    return [rel for rel in files if not any(match_glob(rel, pattern) for pattern in task.scope)]


def hash_diff(task: Task, tree: Path) -> tuple[str, str]:
    files, untracked = _changes(task, tree)
    diff = git._diff(tree, task.base_commit, files, untracked)
    return diff, hashlib.sha256(diff.encode("utf-8", errors="surrogateescape")).hexdigest()
