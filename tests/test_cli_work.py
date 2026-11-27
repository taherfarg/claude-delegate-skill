from pathlib import Path

import pytest

from delegate import state
from delegate.cli import main
from delegate.config import load_config


def test_work_writes_and_records_codex(repo, fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CODEX", "write:src/a.py:x=1\n")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["src/**"], cfg)

    brief = repo / "b.md"
    brief.write_text("implement x=1 in src/a.py\n", encoding="utf-8")

    code = main(["work", "t1", "--brief", str(brief)])
    assert code == 0

    assert (repo / "src/a.py").is_file()
    assert (repo / "src/a.py").read_text(encoding="utf-8") == "x=1\n"

    reloaded = state.load_task(repo, "t1")
    assert reloaded.contributors == ["codex"]
    assert len(reloaded.events) == 1
    event = reloaded.events[0]
    assert event["type"] == "work"
    assert event["exit"] == 0
    assert event["timed_out"] is False
    assert event["unavailable"] is False
    assert event["diff_hash"] == state.hash_diff(reloaded, repo)[1]

    out = capsys.readouterr().out
    assert "Wrote src/a.py" in out
    assert "src/a.py" in out


def test_work_refuses_touched_dirty(repo, fakes, monkeypatch, capsys):
    readme = repo / "README.md"
    readme.write_text("User edit\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)

    with readme.open("a", encoding="utf-8") as stream:
        stream.write("Task edit\n")

    brief = repo / "b.md"
    brief.write_text("edit readme\n", encoding="utf-8")

    code = main(["work", "t1", "--brief", str(brief)])
    assert code == 1
    err = capsys.readouterr().err
    assert "commit or stash" in err


def test_work_timeout_reported(repo, fakes, monkeypatch, capsys):
    (repo / "delegate.toml").write_text("[timeouts]\nworker = 2\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_CODEX", "hang")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)

    brief = repo / "b.md"
    brief.write_text("hang work\n", encoding="utf-8")

    code = main(["work", "t1", "--brief", str(brief)])
    assert code == 1

    err = capsys.readouterr().err
    assert "timed out" in err

    reloaded = state.load_task(repo, "t1")
    assert len(reloaded.events) == 1
    assert reloaded.events[0]["timed_out"] is True


def test_scope_add(repo, monkeypatch):
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["src/**"], cfg)

    task_before = state.load_task(repo, "t1")
    assert state.out_of_scope(task_before, ["x.py"]) == ["x.py"]

    code = main(["scope", "t1", "add", "x.py"])
    assert code == 0

    task_after = state.load_task(repo, "t1")
    assert state.out_of_scope(task_after, ["x.py"]) == []


def test_unknown_task(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    brief = repo / "b.md"
    brief.write_text("some brief\n", encoding="utf-8")

    code = main(["work", "nope", "--brief", str(brief)])
    assert code == 1
    err = capsys.readouterr().err
    assert "no task named nope" in err


def test_work_unavailable_reported(repo, fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CODEX", "quota")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)

    brief = repo / "b.md"
    brief.write_text("quota work\n", encoding="utf-8")

    code = main(["work", "t1", "--brief", str(brief)])
    assert code == 1

    err = capsys.readouterr().err
    assert "unavailable" in err

    reloaded = state.load_task(repo, "t1")
    assert len(reloaded.events) == 1
    assert reloaded.events[0]["unavailable"] is True


def test_task_new_cli(repo, monkeypatch):
    monkeypatch.chdir(repo)
    code = main(["task", "new", "t1", "--title", "CLI Task", "--files", "src/**", "tests/**"])
    assert code == 0

    task = state.load_task(repo, "t1")
    assert task.id == "t1"
    assert task.title == "CLI Task"
    assert task.scope == ["src/**", "tests/**"]
    assert task.worktree is None


def test_task_new_worktree_cli(repo, monkeypatch):
    monkeypatch.chdir(repo)
    code = main(["task", "new", "t2", "--title", "WT Task", "--files", "**", "--worktree"])
    assert code == 0

    task = state.load_task(repo, "t2")
    assert task.worktree is not None
    assert Path(task.worktree).is_dir()


def test_status_cli(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "task 1", ["src/**"], cfg)

    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")

    code = main(["status", "t1"])
    assert code == 0
    out = capsys.readouterr().out
    assert "changed files:" in out
    assert "notes.txt" in out
    assert "out of scope:" in out


def test_status_all_cli(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "task 1", ["src/**"], cfg)
    state.create_task(repo, "t2", "task 2", ["**"], cfg)

    code = main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "task: t1" in out
    assert "task: t2" in out
