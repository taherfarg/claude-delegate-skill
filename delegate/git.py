import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path


class DelegateError(Exception):
    """An error to report to the CLI user."""


def _run(cwd: Path, *args: str, index: Path | None = None, fallback: str = "") -> str:
    env = {**os.environ, "LC_ALL": "C"}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(
            ["git", "--literal-pathspecs", "-c", "core.quotepath=false", *args],
            cwd=cwd, capture_output=True, encoding="utf-8", errors="surrogateescape", env=env,
        )
    except OSError as exc:
        raise DelegateError(str(exc)) from exc
    if result.returncode:
        reason = result.stderr.strip().removeprefix("fatal: ")
        if reason.startswith("not a git repository"):
            reason = "not a git repository"
        raise DelegateError(reason or fallback or f"git {args[0]} failed")
    return result.stdout


def main_root(cwd: Path) -> Path:
    common_dir = _run(cwd, "rev-parse", "--git-common-dir").removesuffix("\n")
    return (cwd / common_dir).resolve().parent


def head(tree: Path) -> str:
    return _run(tree, "rev-parse", "--verify", "--quiet", "HEAD",
                fallback="repository has no commits").strip()


def _project_files(paths: Iterable[str]) -> set[str]:
    return {path for path in paths if path and not path.startswith(".delegate/")}


_CRLF_CONFIG = ("-c", "core.safecrlf=false")  # use the user's autocrlf everywhere so status, listing and hashing agree (forcing it off makes CRLF files look changed on Windows)


def status_files(tree: Path) -> list[str]:
    status = _run(tree, *_CRLF_CONFIG, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    return sorted(_project_files(entry[3:] for entry in status.split("\0")))


def file_sha(tree: Path, rel: str) -> str:
    path = tree / rel
    try:
        if path.is_symlink():
            return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
        if not path.is_file():
            return "<deleted>"
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except (FileNotFoundError, NotADirectoryError):
        return "<deleted>"
    except PermissionError:
        return "<unreadable>"


def _refresh(tree: Path) -> None:
    # Refresh cached stat info like `git status` does; without it `git diff --name-only`
    # can disagree with status on files touched within the same second ("racy git").
    try:
        _run(tree, *_CRLF_CONFIG, "update-index", "-q", "--refresh")
    except DelegateError:
        pass  # non-zero just means some entries still differ, which is expected


def _changes_since(tree: Path, base: str) -> tuple[set[str], set[str]]:
    _refresh(tree)
    tracked =_run(tree, *_CRLF_CONFIG, "diff", "--name-only", "--no-renames", "-z", base, "--")
    untracked = _run(tree, "ls-files", "--others", "--exclude-standard", "-z")
    return _project_files(tracked.split("\0")), _project_files(untracked.split("\0"))


def _diff(tree: Path, base: str, files: list[str], untracked: list[str]) -> str:
    if not files:
        return ""
    args = (*_CRLF_CONFIG, "diff", "--no-color", "--no-ext-diff", base, "--", *files)
    if not untracked:
        return _run(tree, *args)

    # Intent-to-add lets Git diff new files without changing the user's staging area.
    index = tree / _run(tree, "rev-parse", "--git-path", "index").removesuffix("\n")
    with tempfile.TemporaryDirectory() as directory:
        scratch_index = Path(directory) / "index"
        if index.exists():
            shutil.copyfile(index, scratch_index)
        _run(tree, *_CRLF_CONFIG, "add", "-N", "--", *untracked, index=scratch_index)
        return _run(tree, *args, index=scratch_index)
