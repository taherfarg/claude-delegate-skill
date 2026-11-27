import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import runner, state
from .config import Config, match_glob
from .git import DelegateError
from .state import Task


@dataclass
class Evaluation:
    risk: str
    diff_hash: str
    files: list[str]
    blockers: list[str]
    next_cmd: str
    phase: str


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


def evaluate(repo: Path, task: Task, cfg: Config) -> Evaluation:
    tree = state.task_tree(repo, task)
    changed = state.changed_files(task, tree)
    _, current_hash = state.hash_diff(task, tree)
    risk = compute_risk(changed, cfg)

    blockers: list[str] = []

    # B1: no changed files
    if not changed:
        blockers.append("no changes")

    # B2: out of scope
    out_scope = state.out_of_scope(task, changed)
    for f in out_scope:
        blockers.append(f"out of scope: {f}")

    # B3: touched dirty
    dirty = state.touched_dirty(task, tree)
    for f in dirty:
        blockers.append(f"your uncommitted edits were changed: {f}")

    # B4: no gates configured and not allow_no_gates
    if not cfg.gates and not cfg.allow_no_gates:
        blockers.append("no gates configured (add [gates] to delegate.toml)")

    # B5: each configured gate without a pass result at the current hash
    for name in cfg.gates:
        if name not in task.gates:
            blockers.append(f"gate {name}: missing")
        elif task.gates[name].get("diff_hash") != current_hash:
            blockers.append(f"gate {name}: stale")
        elif task.gates[name].get("status") != "pass":
            status = task.gates[name].get("status", "fail")
            blockers.append(f"gate {name}: {status}")

    # Review requirement (medium or high)
    has_valid_approval = bool(task.approval and task.approval.get("diff_hash") == current_hash)
    latest_round = task.reviews[-1] if task.reviews else None

    if risk in ("medium", "high") and not has_valid_approval:
        if not task.reviews:
            blockers.append("review missing")
        elif latest_round.get("diff_hash") != current_hash:
            blockers.append("review stale")
        else:
            if risk == "high":
                required = ["codex", "gemini"]
            elif "codex" in task.contributors:
                required = ["gemini"]
            else:
                required = None  # either one

            if required is not None:
                for r in required:
                    status = latest_round.get("reviewers", {}).get(r, {}).get("status", "missing")
                    if status != "ran":
                        blockers.append(f"reviewer {r}: {status}")
            else:
                codex_status = latest_round.get("reviewers", {}).get("codex", {}).get("status", "missing")
                gemini_status = latest_round.get("reviewers", {}).get("gemini", {}).get("status", "missing")
                if codex_status != "ran" and gemini_status != "ran":
                    blockers.append(f"reviewer codex: {codex_status}")
                    blockers.append(f"reviewer gemini: {gemini_status}")

            for rev_name in ("codex", "gemini"):
                rev_data = latest_round.get("reviewers", {}).get(rev_name, {})
                for f in rev_data.get("findings", []):
                    sev = f.get("severity")
                    if sev in ("blocker", "major", "🔴", "🟡"):
                        fid = f.get("id")
                        tr = task.triage.get(fid, {})
                        if tr.get("decision") != "contest":
                            blockers.append(f"finding {fid} open")

    # B8: high risk: approval missing or diff_hash != current
    if risk == "high" and not has_valid_approval:
        blockers.append(f"human approval required: run 'delegate approve {task.id}' in your own terminal")

    # Phase determination
    if not changed:
        phase = "no-changes"
    elif any(b.startswith("gate ") for b in blockers) or "no gates configured (add [gates] to delegate.toml)" in blockers:
        phase = "needs-verify"
    elif risk in ("medium", "high") and not has_valid_approval and (
        "review missing" in blockers
        or "review stale" in blockers
        or any(b.startswith("reviewer ") for b in blockers)
    ):
        phase = "needs-review"
    elif any(b.startswith("finding ") and b.endswith(" open") for b in blockers):
        phase = "needs-triage"
    elif any(b.startswith("human approval required") for b in blockers):
        phase = "needs-approval"
    elif blockers:
        phase = "needs-verify"
    else:
        phase = "ready"

    cmd_map = {
        "no-changes": f"delegate work {task.id}",
        "needs-verify": f"delegate verify {task.id}",
        "needs-review": f"delegate review {task.id}",
        "needs-triage": f"delegate triage {task.id}",
        "needs-approval": f"delegate approve {task.id}",
        "ready": f"delegate done {task.id}",
    }
    next_cmd = cmd_map[phase]

    return Evaluation(
        risk=risk,
        diff_hash=current_hash,
        files=changed,
        blockers=blockers,
        next_cmd=next_cmd,
        phase=phase,
    )


def approve(
    repo: Path,
    task: Task,
    cfg: Config,
    isatty: Callable[[], bool] = sys.stdin.isatty,
    ask: Callable[[str], str] = input,
) -> None:
    if not isatty():
        raise DelegateError("approve must be run by a human in an interactive terminal")

    tree = state.task_tree(repo, task)
    changed = state.changed_files(task, tree)
    _, current_hash = state.hash_diff(task, tree)
    risk = compute_risk(changed, cfg)

    print(f"risk: {risk}")
    if changed:
        print("changed files:")
        for f in changed:
            print(f"  {f}")
    if cfg.gates:
        print("gates:")
        for name in cfg.gates:
            status = task.gates.get(name, {}).get("status", "missing")
            print(f"  {name}: {status}")
    latest_round = task.reviews[-1] if task.reviews else None
    if latest_round:
        print("findings:")
        for rev_name in ("codex", "gemini"):
            rev_data = latest_round.get("reviewers", {}).get(rev_name, {})
            for f in rev_data.get("findings", []):
                fid = f.get("id")
                tr = task.triage.get(fid, {}).get("decision", "untriaged")
                print(f"  {fid} ({f.get('severity', '')}): {tr}")

    answer = ask(f"Type task id '{task.id}' to approve: ")
    if answer.strip() != task.id:
        raise DelegateError(f"approval aborted: entered '{answer.strip()}', expected '{task.id}'")

    has_b6_b7_issue = False
    if not task.reviews or latest_round.get("diff_hash") != current_hash:
        has_b6_b7_issue = True
    else:
        if risk == "high":
            for r in ("codex", "gemini"):
                if latest_round.get("reviewers", {}).get(r, {}).get("status") != "ran":
                    has_b6_b7_issue = True
        elif "codex" in task.contributors:
            if latest_round.get("reviewers", {}).get("gemini", {}).get("status") != "ran":
                has_b6_b7_issue = True
        else:
            c_stat = latest_round.get("reviewers", {}).get("codex", {}).get("status")
            g_stat = latest_round.get("reviewers", {}).get("gemini", {}).get("status")
            if c_stat != "ran" and g_stat != "ran":
                has_b6_b7_issue = True

        for rev_name in ("codex", "gemini"):
            rev_data = latest_round.get("reviewers", {}).get(rev_name, {})
            for f in rev_data.get("findings", []):
                if f.get("severity") in ("blocker", "major", "🔴", "🟡"):
                    fid = f.get("id")
                    if task.triage.get(fid, {}).get("decision") != "contest":
                        has_b6_b7_issue = True

    override = (risk != "high") or has_b6_b7_issue

    task.approval = {
        "diff_hash": current_hash,
        "at": datetime.now(timezone.utc).isoformat(),
        "override": override,
    }
    state.save_task(repo, task)
