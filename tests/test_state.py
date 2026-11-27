import re
import subprocess
from pathlib import Path

import pytest

from delegate import git, state
from delegate.config import load_config


def test_create_records_base_and_excludes_state_dir(repo):
    task = state.create_task(repo, "t1", "x", ["src/**"], load_config(repo))

    assert task.base_commit == git.head(repo)
    assert ".delegate/" in (repo / ".git/info/exclude").read_text(encoding="utf-8").splitlines()
    assert (state.task_dir(repo, "t1") / "state.json").is_file()
    assert not (repo / ".gitignore").exists()


@pytest.mark.parametrize("task_id", ["../x", "", "a b"])
def test_bad_id_rejected(repo, task_id):
    with pytest.raises(git.DelegateError):
        state.create_task(repo, task_id, "x", ["src/**"], load_config(repo))


def test_duplicate_task_rejected(repo):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "x", ["src/**"], cfg)
    with pytest.raises(git.DelegateError):
        state.create_task(repo, "t1", "duplicate", ["**"], cfg)
    assert state.load_task(repo, "t1") == task


def test_dirty_files_excluded_until_touched(repo):
    readme = repo / "README.md"
    readme.write_text("User edit\n", encoding="utf-8")
    task = state.create_task(repo, "t1", "x", ["**"], load_config(repo))
    assert "README.md" not in state.changed_files(task, repo)

    with readme.open("a", encoding="utf-8") as stream:
        stream.write("Task edit\n")
    assert state.touched_dirty(task, repo) == ["README.md"]
    assert "README.md" in state.changed_files(task, repo)


def test_untracked_new_file_in_diff_and_hash_changes(repo):
    task = state.create_task(repo, "t1", "x", ["src/**"], load_config(repo))
    path = repo / "src/a.py"
    path.parent.mkdir()
    path.write_text("x = 1\n", encoding="utf-8")

    assert "src/a.py" in state.changed_files(task, repo)
    diff, first_hash = state.hash_diff(task, repo)
    assert "src/a.py" in diff and "+x = 1" in diff
    path.write_text("x = 2\n", encoding="utf-8")
    assert state.hash_diff(task, repo)[1] != first_hash


def test_hash_stable_with_autocrlf(repo):
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=repo, check=True)
    task = state.create_task(repo, "t1", "x", ["**"], load_config(repo))
    (repo / "windows.txt").write_bytes(b"one\r\ntwo\r\n")

    first = state.hash_diff(task, repo)
    assert "+one" in first[0]
    assert first == state.hash_diff(task, repo)


def test_unicode_and_space_paths(repo):
    task = state.create_task(repo, "t1", "x", ["**"], load_config(repo))
    rel = "مجلد/ملف.py"
    path = repo / rel
    path.parent.mkdir()
    path.write_text("x = 1\n", encoding="utf-8")

    assert rel in state.changed_files(task, repo)


def test_out_of_scope(repo):
    task = state.create_task(repo, "t1", "x", ["src/**"], load_config(repo))
    assert state.out_of_scope(task, ["src/a.py", "x.py"]) == ["x.py"]


def test_not_a_repo_and_no_commits(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    cfg = load_config(tmp_path)
    with pytest.raises(git.DelegateError, match="not a git repository"):
        state.create_task(tmp_path, "t1", "x", ["**"], cfg)

    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    with pytest.raises(git.DelegateError, match="no commits"):
        state.create_task(tmp_path, "t1", "x", ["**"], cfg)


def test_lock_busy(repo):
    state.create_task(repo, "t1", "x", ["**"], load_config(repo))
    with state.task_lock(repo, "t1"):
        with pytest.raises(git.DelegateError, match="is busy"):
            with state.task_lock(repo, "t1"):
                pytest.fail("Acquired an already held lock")


def test_worktree_task_state_in_main_repo(repo):
    task = state.create_task(repo, "t1", "x", ["**"], load_config(repo), worktree=True)
    tree = repo.with_name(f"{repo.name}-wt-t1")
    assert tree.is_dir()
    assert state.task_tree(repo, task) == tree
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=tree, check=True,
        capture_output=True, encoding="utf-8",
    )
    assert branch.stdout.strip() == "delegate/t1"
    assert (repo / ".delegate/tasks/t1/state.json").is_file()
    assert state.load_task(tree, "t1") == task


def test_changes_since_respects_autocrlf(repo):
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=repo, check=True)
    base = git.head(repo)
    (repo / "README.md").write_bytes(b"# Test project\r\n")
    tracked, _ = git._changes_since(repo, base)
    # Whether git counts a line-ending-only edit as a change differs by platform;
    # what must hold everywhere is that our listing agrees with git status.
    assert ("README.md" in tracked) == ("README.md" in git.status_files(repo))


def test_lock_guard_releases_msvcrt_lock(tmp_path, monkeypatch):
    import os
    if os.name != "nt":
        import sys
        import types
        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_LOCK = 1
        fake_msvcrt.LK_UNLCK = 2
        calls = []
        fake_msvcrt.locking = lambda fd, mode, n: calls.append(mode)
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        monkeypatch.setattr(os, "name", "nt")
        with state._lock_guard(tmp_path):
            pass
        assert calls == [fake_msvcrt.LK_LOCK, fake_msvcrt.LK_UNLCK]
    else:
        import msvcrt
        unlocked = []
        orig_locking = msvcrt.locking
        def spy_locking(fd, mode, n):
            if mode == msvcrt.LK_UNLCK:
                unlocked.append(mode)
            return orig_locking(fd, mode, n)
        monkeypatch.setattr(msvcrt, "locking", spy_locking)
        with state._lock_guard(tmp_path):
            pass
        assert len(unlocked) == 1


@pytest.mark.parametrize("task_id", [
    "CON", "con", "prn", "AUX", "nul", "COM1", "com9", "LPT1", "lpt9",
    "con.txt", "NUL.tar.gz", "aux.json", "com3.dat",
    "task.", "foo..", ".", "..",
])
def test_windows_reserved_and_dot_task_ids_rejected(repo, task_id):
    with pytest.raises(git.DelegateError):
        state.create_task(repo, task_id, "x", ["src/**"], load_config(repo))


def test_task_tree_missing_worktree_raises_delegate_error(repo):
    task = state.create_task(repo, "t1", "x", ["**"], load_config(repo), worktree=True)
    tree = Path(task.worktree)
    assert tree.is_dir()
    import shutil
    shutil.rmtree(tree)
    with pytest.raises(git.DelegateError, match=re.escape(f"worktree for task t1 is missing: {task.worktree}")):
        state.task_tree(repo, task)


def test_file_sha_permission_error_returns_unreadable(repo, monkeypatch):
    file_path = repo / "locked.txt"
    file_path.write_text("content", encoding="utf-8")
    def fail_open(*args, **kwargs):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(Path, "open", fail_open)
    assert git.file_sha(repo, "locked.txt") == "<unreadable>"
