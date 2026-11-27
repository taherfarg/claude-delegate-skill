import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .state import Task


def home() -> Path:
    env = os.environ.get("DELEGATE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".delegate"


def record(
    task: Task,
    cfg: Config,
    outcome: str,
    evaluation: object | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    ended = datetime.now(timezone.utc).isoformat()

    try:
        start_dt = datetime.fromisoformat(task.created)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(ended)
        wall_seconds = round(max(0.0, (end_dt - start_dt).total_seconds()), 1)
    except Exception:
        wall_seconds = 0.0

    work_runs = sum(1 for e in task.events if e.get("type") == "work")
    worker_timeouts = sum(1 for e in task.events if e.get("type") == "work" and e.get("timed_out"))
    review_rounds = len(task.reviews)

    findings: dict[str, dict[str, int]] = {
        "codex": {"blocker": 0, "major": 0, "nit": 0},
        "gemini": {"blocker": 0, "major": 0, "nit": 0},
    }
    reviewer_status: dict[str, dict[str, int]] = {
        "codex": {"ran": 0, "not_run": 0, "unavailable": 0},
        "gemini": {"ran": 0, "not_run": 0, "unavailable": 0},
    }

    for r in task.reviews:
        reviewers_dict = r.get("reviewers", {})
        for rev_name, rev_data in reviewers_dict.items():
            if rev_name not in findings:
                findings[rev_name] = {"blocker": 0, "major": 0, "nit": 0}
            for f in rev_data.get("findings", []):
                sev = f.get("severity") if isinstance(f, dict) else getattr(f, "severity", None)
                if sev in findings[rev_name]:
                    findings[rev_name][sev] += 1

            if rev_name not in reviewer_status:
                reviewer_status[rev_name] = {"ran": 0, "not_run": 0, "unavailable": 0}
            st = rev_data.get("status") if isinstance(rev_data, dict) else getattr(rev_data, "status", None)
            if st in reviewer_status[rev_name]:
                reviewer_status[rev_name][st] += 1

    triage: dict[str, int] = {"fix": 0, "contest": 0}
    for entry in task.triage.values():
        dec = entry.get("decision") if isinstance(entry, dict) else getattr(entry, "decision", None)
        if dec in triage:
            triage[dec] += 1

    gates: dict[str, int] = {"pass": 0, "fail": 0, "timeout": 0}
    for entry in task.gates.values():
        st = entry.get("status") if isinstance(entry, dict) else getattr(entry, "status", None)
        if st in gates:
            gates[st] += 1

    override = bool(
        (task.approval and task.approval.get("override", False))
        or getattr(evaluation, "override", False)
    )

    risk = getattr(evaluation, "risk", None) if evaluation is not None else None

    record_data: dict[str, Any] = {
        "project": cfg.project,
        "task": task.id,
        "risk": risk,
        "outcome": outcome,
        "started": task.created,
        "ended": ended,
        "wall_seconds": wall_seconds,
        "work_runs": work_runs,
        "worker_timeouts": worker_timeouts,
        "review_rounds": review_rounds,
        "findings": findings,
        "triage": triage,
        "gates": gates,
        "reviewer_status": reviewer_status,
        "override": override,
    }

    target_path = path or (home() / "metrics.jsonl")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record_data) + "\n")

    return record_data


def report(path: Path) -> str:
    if not path.is_file() or path.stat().st_size == 0:
        return "no tasks recorded yet"

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        return "no tasks recorded yet"

    outcomes: dict[str, int] = {}
    wall_times: list[float] = []
    rounds: list[int] = []
    total_fixes = 0
    total_contests = 0

    findings_by_reviewer: dict[str, dict[str, int]] = {
        "codex": {"blocker": 0, "major": 0, "nit": 0},
        "gemini": {"blocker": 0, "major": 0, "nit": 0},
    }
    status_by_reviewer: dict[str, dict[str, int]] = {
        "codex": {"ran": 0, "not_run": 0, "unavailable": 0},
        "gemini": {"ran": 0, "not_run": 0, "unavailable": 0},
    }

    for r in records:
        outcome = r.get("outcome", "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

        wall_times.append(float(r.get("wall_seconds", 0.0)))
        rounds.append(int(r.get("review_rounds", 0)))

        triage_info = r.get("triage", {})
        total_fixes += triage_info.get("fix", 0)
        total_contests += triage_info.get("contest", 0)

        for rev, counts in r.get("findings", {}).items():
            if rev not in findings_by_reviewer:
                findings_by_reviewer[rev] = {"blocker": 0, "major": 0, "nit": 0}
            for sev in ("blocker", "major", "nit"):
                findings_by_reviewer[rev][sev] += counts.get(sev, 0)

        for rev, st_counts in r.get("reviewer_status", {}).items():
            if rev not in status_by_reviewer:
                status_by_reviewer[rev] = {"ran": 0, "not_run": 0, "unavailable": 0}
            for st in ("ran", "not_run", "unavailable"):
                status_by_reviewer[rev][st] += st_counts.get(st, 0)

    median_wall = statistics.median(wall_times) if wall_times else 0.0
    mean_rounds = statistics.mean(rounds) if rounds else 0.0
    total_triaged = total_fixes + total_contests
    contest_rate = (total_contests / total_triaged * 100.0) if total_triaged > 0 else 0.0

    lines: list[str] = []
    outcome_str = ", ".join(f"{k}: {v}" for k, v in sorted(outcomes.items()))
    lines.append(f"tasks by outcome: {outcome_str}")
    lines.append(f"median wall time: {median_wall:.1f}s")
    lines.append(f"mean rounds: {mean_rounds:.1f}")
    lines.append(f"contest rate: {contest_rate:.1f}% ({total_contests}/{total_triaged})")

    lines.append("findings per reviewer by severity:")
    for rev in sorted(findings_by_reviewer.keys()):
        counts = findings_by_reviewer[rev]
        total_f = sum(counts.values())
        lines.append(
            f"  {rev}: blocker: {counts['blocker']}, major: {counts['major']}, nit: {counts['nit']} (total: {total_f})"
        )

    lines.append("not_run/unavailable per reviewer:")
    for rev in sorted(status_by_reviewer.keys()):
        st = status_by_reviewer[rev]
        lines.append(
            f"  {rev}: not_run: {st['not_run']}, unavailable: {st['unavailable']} (ran: {st['ran']})"
        )

    return "\n".join(lines)
