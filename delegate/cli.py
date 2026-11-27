import argparse
import sys
from typing import NoReturn

from . import __version__
from .git import DelegateError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(1, f"{self.prog}: {message}\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="delegate")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    try:
        parser.parse_args(argv)
    except DelegateError as exc:
        print(f"delegate: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0
