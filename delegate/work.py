import shutil
import tempfile
from pathlib import Path

from . import git, runner, state
from .config import Config
from .git import DelegateError
from .runner import RunResult
from .state import Task


def diff_stat(task: Task, tree: Path) -> str:
    files, untracked = state._changes(task, tree)
    if not files:
        return ""
    config = git._CRLF_CONFIG  # same line-ending handling as the change listing and hash
    args = (*config, "diff", "--stat", "--no-color", "--no-ext-diff", task.base_commit, "--", *files)
    if not untracked:
        return git._run(tree, *args)
    index = tree / git._run(tree, "rev-parse", "--git-path", "index").removesuffix("\n")
    with tempfile.TemporaryDirectory() as directory:
        scratch_index = Path(directory) / "index"
        if index.exists():
            shutil.copyfile(index, scratch_index)
        git._run(tree, *config, "add", "-N", "--", *untracked, index=scratch_index)
        return git._run(tree, *args, index=scratch_index)


def run_worker(repo: Path, task: Task, cfg: Config, brief: Path) -> RunResult:
    tree = state.task_tree(repo, task)
    dirty = state.touched_dirty(task, tree)
    if dirty:
        raise DelegateError(f"commit or stash your edits to {dirty[0]} first")

    directory = state.task_dir(repo, task.id)
    directory.mkdir(parents=True, exist_ok=True)
    if not brief.is_file():
        raise DelegateError(f"brief file not found: {brief}")

    target_brief = directory / brief.name
    if brief.resolve() != target_brief.resolve():
        shutil.copy2(brief, target_brief)
    if brief.name != "brief.md":
        shutil.copy2(brief, directory / "brief.md")

    # The worker's sandbox can write inside the repo, which includes .delegate/. Snapshot
    # every other task's state so a worker can't forge gate results or approvals there.
    # (This task's own state is rewritten from memory by save_task below.)
    tasks_root = directory.parent
    snapshot = {
        p: p.read_bytes() for p in tasks_root.glob("*/state.json") if p.parent != directory
    }

    diff_before, hash_before = state.hash_diff(task, tree)
    result_path = directory / "result.md"
    result_path.unlink(missing_ok=True)
    log_path = directory / "work.log"

    cmd = [
        runner.resolve("codex"),
        "exec",
        "-C",
        str(tree),
        "-s",
        "workspace-write",
        "-o",
        str(result_path),
        "-",
    ]
    result = runner.run(
        cmd,
        tree,
        log_path,
        timeout=cfg.timeouts.worker,
        stdin_path=target_brief,
    )

    tampered = []
    for path, original in snapshot.items():
        if not path.exists() or path.read_bytes() != original:
            path.write_bytes(original)
            tampered.append(path.parent.name)
    if tampered:
        task.events.append({"type": "tamper", "restored": tampered})

    diff_after, hash_after = state.hash_diff(task, tree)
    if hash_after != hash_before and "codex" not in task.contributors:
        task.contributors.append("codex")

    event = {
        "type": "work",
        "diff_hash": hash_after,
        "exit": result.exit_code,
        "timed_out": result.timed_out,
        "unavailable": result.unavailable,
        "duration": result.duration,
    }
    task.events.append(event)
    state.save_task(repo, task)
    return result
