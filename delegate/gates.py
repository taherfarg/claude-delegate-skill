from datetime import datetime, timezone
from pathlib import Path

from . import runner, state
from .config import Config, match_glob
from .state import Task


def compute_risk(files: list[str], cfg: Config) -> str:
    norm_files = [path.replace("\\", "/") for path in files]
    if any(any(match_glob(path, pattern) for pattern in cfg.risk_high) for path in norm_files):
        return "high"
    if norm_files and all(any(match_glob(path, pattern) for pattern in cfg.risk_low) for path in norm_files):
        return "low"
    return "medium"


def run_gates(repo: Path, task: Task, cfg: Config) -> dict[str, dict]:
    tree = state.task_tree(repo, task)
    _, before_hash = state.hash_diff(task, tree)
    results: dict[str, dict] = {}
    directory = state.task_dir(repo, task.id)
    directory.mkdir(parents=True, exist_ok=True)

    modified = False
    for name, cmd in cfg.gates.items():
        log_path = directory / f"gate-{name}.log"
        result = runner.run_shell(cmd, cwd=tree, log_path=log_path, timeout=cfg.timeouts.gate)
        if result.timed_out:
            status = "timeout"
            exit_code = None
        elif result.exit_code == 0:
            status = "pass"
            exit_code = 0
        else:
            status = "fail"
            exit_code = result.exit_code

        iso_now = datetime.now(timezone.utc).isoformat()
        results[name] = {
            "status": status,
            "exit": exit_code,
            "diff_hash": before_hash,
            "log": str(log_path),
            "at": iso_now,
        }

        _, current_hash = state.hash_diff(task, tree)
        if current_hash != before_hash:
            modified = True

    _, after_hash = state.hash_diff(task, tree)
    if modified or after_hash != before_hash:
        for record in results.values():
            record["status"] = "fail"
            record["reason"] = "gate modified files"

    task.gates.update(results)
    state.save_task(repo, task)
    return results
