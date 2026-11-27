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


def _agy_worker_cmd(cfg: Config, tree: Path, brief: Path) -> list[str]:
    # Headless agy ends the whole run (with no output) on one denied shell command or a
    # read outside its folders, so say so before anything else. Edits only, never a shell.
    skills = Path.home() / ".agents" / "skills"
    return [
        runner.resolve("agy"),
        "-p",
        "IMPORTANT: your shell/terminal/command tool is DISABLED in this session. A single "
        "attempt to run ANY command (ls, cat, git, python, dir...) aborts the whole session, "
        "so you cannot run tests; write the code carefully instead. Read and edit files only "
        f"with your file tools, only inside {tree.resolve().as_posix()}; you may also read "
        f"{brief.resolve().as_posix()}. Now read {brief.resolve().as_posix()} and do "
        "exactly what it says.",
        "--mode",
        "accept-edits",
        "--model",
        cfg.gemini_model,
        "--add-dir",
        str(brief.parent),
        *(["--add-dir", str(skills)] if skills.is_dir() else []),
        "--print-timeout",
        f"{cfg.timeouts.worker}s",
    ]


def run_worker(repo: Path, task: Task, cfg: Config, brief: Path, worker: str = "codex") -> RunResult:
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

    if worker == "gemini":
        # agy can't read the git-excluded .delegate/, so hand it the brief from outside the repo.
        exchange = Path(tempfile.mkdtemp(prefix="delegate-work-"))
        try:
            shutil.copy2(target_brief, exchange / "brief.md")
            result = runner.run(
                _agy_worker_cmd(cfg, tree, exchange / "brief.md"),
                tree,
                log_path,
                timeout=cfg.timeouts.worker,
                stdout_path=result_path,
            )
        finally:
            shutil.rmtree(exchange, ignore_errors=True)
    else:
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
    if hash_after != hash_before and worker not in task.contributors:
        task.contributors.append(worker)

    event = {
        "type": "work",
        "worker": worker,
        "diff_hash": hash_after,
        "exit": result.exit_code,
        "timed_out": result.timed_out,
        "unavailable": result.unavailable,
        "duration": result.duration,
    }
    task.events.append(event)
    state.save_task(repo, task)
    return result
