from pathlib import Path

import pytest

from delegate import state
from delegate.cli import main
from delegate.config import load_config


def test_review_both_ran_findings_sorted(repo, fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CODEX", "findings")
    monkeypatch.setenv("FAKE_AGY", "none")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)

    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src/b.py").write_text("y = 2\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert len(reloaded.reviews) == 1
    rev = reloaded.reviews[0]
    assert rev["round"] == 1
    assert rev["reviewers"]["codex"]["status"] == "ran"
    assert len(rev["reviewers"]["codex"]["findings"]) == 2
    f1 = rev["reviewers"]["codex"]["findings"][0]
    f2 = rev["reviewers"]["codex"]["findings"][1]
    assert f1["id"] == "codex-1-1"
    assert f2["id"] == "codex-1-2"
    assert rev["reviewers"]["gemini"]["status"] == "ran"
    assert len(rev["reviewers"]["gemini"]["findings"]) == 0

    out = capsys.readouterr().out
    assert "codex-1-1" in out
    assert "codex-1-2" in out
    assert "codex: ran" in out
    assert "gemini: ran" in out

    idx1 = out.index("codex-1-1")
    idx2 = out.index("codex-1-2")
    assert idx1 < idx2


def test_invalid_then_valid_retry(repo, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "prose,none")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert reloaded.reviews[0]["reviewers"]["gemini"]["status"] == "ran"
    assert (fakes / "agy.count").read_text(encoding="ascii") == "2"


def test_invalid_twice_not_run(repo, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "prose")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert reloaded.reviews[0]["reviewers"]["gemini"]["status"] == "not_run"
    assert (fakes / "agy.count").read_text(encoding="ascii") == "2"


def test_quota_unavailable(repo, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "quota")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert reloaded.reviews[0]["reviewers"]["gemini"]["status"] == "unavailable"
    assert (fakes / "agy.count").read_text(encoding="ascii") == "1"


def test_secret_blocks_send(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "secret.py").write_text("token = 'ghp_" + "a" * 36 + "'\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 2

    out = capsys.readouterr().out
    assert "ghp_" in out
    assert "github-token" in out


def test_round_cap(repo, fakes, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")

    task.reviews = [
        {"round": 1, "diff_hash": "h1"},
        {"round": 2, "diff_hash": "h2"},
        {"round": 3, "diff_hash": "h3"},
    ]
    state.save_task(repo, task)

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 2
    out = capsys.readouterr().out
    assert "review cap reached (3)" in out


def test_lockfile_omitted_from_patch_but_in_hash(repo, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "none")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "test task", ["**"], cfg)

    _, hash_before = state.hash_diff(task, repo)
    (repo / "package-lock.json").write_text('{\n  "name": "test"\n}\n', encoding="utf-8")
    _, hash_after = state.hash_diff(task, repo)
    assert hash_after != hash_before

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    patch_file = state.task_dir(repo, "t1") / "diff.patch"
    assert patch_file.is_file()
    patch_text = patch_file.read_text(encoding="utf-8")
    assert "# omitted from review: package-lock.json" in patch_text
    assert "diff --git a/package-lock.json" not in patch_text


def test_brief_points_to_patch_in_taskdir(repo, fakes, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "none")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    brief_file = state.task_dir(repo, "t1") / "review_brief.md"
    assert brief_file.is_file()
    brief_text = brief_file.read_text(encoding="utf-8")
    patch_file = state.task_dir(repo, "t1") / "diff.patch"
    expected_patch_posix = patch_file.resolve().as_posix()
    assert expected_patch_posix in brief_text
    assert ".delegate/tasks/t1/diff.patch" in expected_patch_posix


def test_triage_records_and_same_as(repo, fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CODEX", "findings")
    monkeypatch.setenv("FAKE_AGY", "none")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src/b.py").write_text("y = 2\n", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 0

    code = main(["triage", "t1", "codex-1-1", "contest", "intentional design"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert "codex-1-1" in reloaded.triage
    entry1 = reloaded.triage["codex-1-1"]
    assert entry1["decision"] == "contest"
    assert entry1["reason"] == "intentional design"
    assert entry1["same_as"] is None
    assert entry1["diff_hash"]
    assert entry1["at"]

    code = main(["triage", "t1", "codex-1-2", "fix", "", "--same-as", "codex-1-1"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert "codex-1-2" in reloaded.triage
    entry2 = reloaded.triage["codex-1-2"]
    assert entry2["decision"] == "fix"
    assert entry2["reason"] == "intentional design"
    assert entry2["same_as"] == "codex-1-1"
    assert entry2["diff_hash"]
    assert entry2["at"]

    code = main(["triage", "t1", "nonexistent-id", "fix", "reason"])
    assert code == 1
    err = capsys.readouterr().err
    assert "unknown finding id: nonexistent-id" in err


def test_review_no_changes(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 2
    out = capsys.readouterr().out
    assert "nothing to review" in out


def test_review_touched_dirty(repo, monkeypatch, capsys):
    readme = repo / "README.md"
    readme.write_text("User edit\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)

    with readme.open("a", encoding="utf-8") as stream:
        stream.write("Task edit\n")

    code = main(["review", "t1", "--goal", "Check code"])
    assert code == 1
    err = capsys.readouterr().err
    assert "commit or stash" in err


def test_criteria_file_and_missing(repo, fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "none")
    monkeypatch.chdir(repo)
    cfg = load_config(repo)
    state.create_task(repo, "t1", "test task", ["**"], cfg)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")

    crit = repo / "crit.md"
    crit.write_text("Custom acceptance criteria", encoding="utf-8")

    code = main(["review", "t1", "--goal", "Goal text", "--criteria-file", str(crit)])
    assert code == 0

    brief_file = state.task_dir(repo, "t1") / "review_brief.md"
    brief_text = brief_file.read_text(encoding="utf-8")
    assert "Custom acceptance criteria" in brief_text

    code_missing = main(["review", "t1", "--goal", "Goal text", "--criteria-file", "nonexistent.md"])
    assert code_missing == 1
    err = capsys.readouterr().err
    assert "criteria file not found" in err
