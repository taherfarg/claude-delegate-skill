import argparse
from pathlib import Path
import sys
from typing import NoReturn

from . import __version__, gates, git, runner, state
from .config import load_config
from .git import DelegateError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(1, f"{self.prog}: {message}\n")


def _cmd_verify(args: argparse.Namespace) -> int:
    repo = git.main_root(Path.cwd())
    cfg = load_config(repo)
    with state.task_lock(repo, args.id):
        task = state.load_task(repo, args.id)
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


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="delegate")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("id")

    try:
        args = parser.parse_args(argv)
        if args.command == "verify":
            return _cmd_verify(args)
    except DelegateError as exc:
        print(f"delegate: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0
