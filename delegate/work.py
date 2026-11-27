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
    config = ("-c", "core.autocrlf=false", "-c", "core.safecrlf=false")
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
