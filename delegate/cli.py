import argparse
import sys
from pathlib import Path
from typing import NoReturn

from . import __version__, gates, git, metrics, review, runner, setup, state
from .config import Config, ConfigError, load_config
from .git import DelegateError
from .review import ReviewRefusal
from .work import diff_stat, run_worker


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(1, f"{self.prog}: {message}\n")


def _load_task(repo: Path, task_id: str) -> state.Task:
    try:
        return state.load_task(repo, task_id)
    except DelegateError as exc:
        if str(exc) == f"task {task_id} does not exist":
            raise DelegateError(f"no task named {task_id}") from exc
        raise


def _cmd_task_new(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    state.create_task(
        repo=repo,
        task_id=args.id,
        title=args.title,
        scope=args.files,
        cfg=cfg,
        worktree=args.worktree,
    )
    return 0


def _cmd_scope_add(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    task_id = args.id
    with state.task_lock(repo, task_id):
        task = _load_task(repo, task_id)
        for pattern in args.files:
            if pattern not in task.scope:
                task.scope.append(pattern)
        state.save_task(repo, task)
    return 0


def _cmd_work(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    task_id = args.id
    brief_path = Path(args.brief)
    if not brief_path.is_absolute():
        brief_path = (Path.cwd() / brief_path).resolve()
    with state.task_lock(repo, task_id):
        task = _load_task(repo, task_id)
        if not brief_path.is_file():
            raise DelegateError(f"brief file not found: {args.brief}")
        result = run_worker(repo, task, cfg, brief_path)
        tree = state.task_tree(repo, task)
        result_file = state.task_dir(repo, task.id) / "result.md"
        if result_file.is_file():
            text = result_file.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                lines = text.splitlines()
                print("\n".join(lines[:15]))
        stat = diff_stat(task, tree)
        if stat:
            print(stat.rstrip())
        if result.exit_code != 0 or result.timed_out or result.unavailable:
            reason = (
                "timed out"
                if result.timed_out
                else ("unavailable" if result.unavailable else f"failed with exit code {result.exit_code}")
            )
            print(f"delegate: worker {reason}", file=sys.stderr)
            tail = runner.log_tail(result.log_path, 20)
            if tail:
                print(tail, file=sys.stderr)
            return 1
        return 0


def _cmd_status(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    if args.id:
        task_id = args.id
        with state.task_lock(repo, task_id):
            task = _load_task(repo, task_id)
            tree = state.task_tree(repo, task)
            changed = state.changed_files(task, tree)
            out_scope = state.out_of_scope(task, changed)
            dirty = state.touched_dirty(task, tree)
            if changed:
                print("changed files:")
                for f in changed:
                    print(f"  {f}")
            if out_scope:
                print("out of scope:")
                for f in out_scope:
                    print(f"  {f}")
            if dirty:
                print("touched dirty:")
                for f in dirty:
                    print(f"  {f}")
        return 0

    tasks_dir = git.main_root(repo) / ".delegate" / "tasks"
    if tasks_dir.is_dir():
        for item in sorted(tasks_dir.iterdir()):
            if (item / "state.json").is_file():
                task_id = item.name
                with state.task_lock(repo, task_id):
                    task = state.load_task(repo, task_id)
                    tree = state.task_tree(repo, task)
                    changed = state.changed_files(task, tree)
                    out_scope = state.out_of_scope(task, changed)
                    dirty = state.touched_dirty(task, tree)
                    print(f"task: {task.id}")
                    if changed:
                        print("  changed files:")
                        for f in changed:
                            print(f"    {f}")
                    if out_scope:
                        print("  out of scope:")
                        for f in out_scope:
                            print(f"    {f}")
                    if dirty:
                        print("  touched dirty:")
                        for f in dirty:
                            print(f"    {f}")
    return 0


def _cmd_verify(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    with state.task_lock(repo, args.id):
        task = _load_task(repo, args.id)
        results = gates.run_gates(repo, task, cfg)
        all_passed = True
        for name, record in results.items():
            status = record["status"]
            if status == "pass":
                print(f"PASS {name}")
            elif status == "timeout":
                print(f"TIMEOUT {name}")
                tail = runner.log_tail(Path(record["log"]))
                if tail:
                    print(tail)
                all_passed = False
            else:
                print(f"FAIL {name}")
                tail = runner.log_tail(Path(record["log"]))
                if tail:
                    print(tail)
                if record.get("reason"):
                    print(record["reason"])
                all_passed = False
        return 0 if all_passed else 2


def _cmd_review(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    task_id = args.id
    criteria = ""
    if args.criteria_file:
        criteria_path = Path(args.criteria_file)
        if not criteria_path.is_absolute():
            criteria_path = (Path.cwd() / criteria_path).resolve()
        if not criteria_path.is_file():
            raise DelegateError(f"criteria file not found: {args.criteria_file}")
        criteria = criteria_path.read_text(encoding="utf-8")

    with state.task_lock(repo, task_id):
        task = _load_task(repo, task_id)
        round_record = review.run_review(repo, task, cfg, goal=args.goal, criteria=criteria)
        all_findings = []
        for reviewer_data in round_record["reviewers"].values():
            all_findings.extend(reviewer_data["findings"])
        all_findings.sort(key=lambda f: (f["file"], f["line"]))
        for f in all_findings:
            icon = review._ICONS.get(f["severity"], "🔴")
            print(f"{f['id']} {icon} {f['file']}:{f['line']} — {f['problem']} — {f['why']}")
        for name in ("codex", "gemini"):
            status = round_record["reviewers"][name]["status"]
            print(f"{name}: {status}")
    return 0


def _cmd_triage(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    task_id = args.id
    with state.task_lock(repo, task_id):
        task = _load_task(repo, task_id)
        review.triage_finding(
            repo=repo,
            task=task,
            finding_id=args.finding_id,
            decision=args.decision,
            reason=args.reason,
            same_as=args.same_as,
        )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        repo = git.main_root(Path.cwd())
        cfg = load_config(repo)
        checks = setup.doctor(cfg=cfg)
    except Exception:
        checks = setup.doctor()
    all_passed = True
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"{status} {name} {detail}")
        if not ok:
            all_passed = False
    return 0 if all_passed else 2


def _cmd_init(repo: Path, cfg: Config, args: argparse.Namespace) -> int:
    target = setup.init(repo)
    print(f"initialized {target.name}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    path = metrics.home() / "metrics.jsonl"
    print(metrics.report(path))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="delegate")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    task_parser = subparsers.add_parser("task")
    task_subparsers = task_parser.add_subparsers(dest="task_command", metavar="ACTION", required=True)
    new_parser = task_subparsers.add_parser("new")
    new_parser.add_argument("id")
    new_parser.add_argument("--title", required=True)
    new_parser.add_argument("--files", nargs="+", required=True)
    new_parser.add_argument("--worktree", action="store_true")

    scope_parser = subparsers.add_parser("scope")
    scope_parser.add_argument("id")
    scope_subparsers = scope_parser.add_subparsers(dest="scope_command", metavar="ACTION", required=True)
    add_parser = scope_subparsers.add_parser("add")
    add_parser.add_argument("files", nargs="+")

    work_parser = subparsers.add_parser("work")
    work_parser.add_argument("id")
    work_parser.add_argument("--brief", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("id", nargs="?", default=None)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("id")

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("id")
    review_parser.add_argument("--goal", required=True)
    review_parser.add_argument("--criteria-file", default=None)

    triage_parser = subparsers.add_parser("triage")
    triage_parser.add_argument("id")
    triage_parser.add_argument("finding_id")
    triage_parser.add_argument("decision", choices=["fix", "contest"])
    triage_parser.add_argument("reason", nargs="?", default="")
    triage_parser.add_argument("--same-as", default=None)

    doctor_parser = subparsers.add_parser("doctor")

    init_parser = subparsers.add_parser("init")

    report_parser = subparsers.add_parser("report")

    try:
        args = parser.parse_args(argv)
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "report":
            return _cmd_report(args)
        repo = git.main_root(Path.cwd())
        cfg = load_config(repo)
        if args.command == "init":
            return _cmd_init(repo, cfg, args)
        if args.command == "task":
            if args.task_command == "new":
                return _cmd_task_new(repo, cfg, args)
        elif args.command == "scope":
            if args.scope_command == "add":
                return _cmd_scope_add(repo, cfg, args)
        elif args.command == "work":
            return _cmd_work(repo, cfg, args)
        elif args.command == "status":
            return _cmd_status(repo, cfg, args)
        elif args.command == "verify":
            return _cmd_verify(repo, cfg, args)
        elif args.command == "review":
            return _cmd_review(repo, cfg, args)
        elif args.command == "triage":
            return _cmd_triage(repo, cfg, args)
    except ReviewRefusal as exc:
        print(exc.message)
        return exc.exit_code
    except (DelegateError, ConfigError) as exc:
        print(f"delegate: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0
