"""The model that wrote the code can't be its only reviewer.

Regression for the 2026-11-27 splitbill experiment: Gemini coded through the
fallback outside the engine, then Gemini's own review was accepted as the
independent one.
"""
import json
from pathlib import Path

import pytest

from delegate import gates, state
from delegate.cli import main
from delegate.config import load_config


def _medium_task(repo, contributors, ran):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    task.contributors.extend(contributors)
    _, h = state.hash_diff(task, state.task_tree(repo, task))
    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {r: {"status": "ran" if r in ran else "unavailable", "findings": []}
                      for r in ("codex", "gemini")},
        "at": "iso",
    })
    state.save_task(repo, task)
    return cfg, task


def test_gemini_written_needs_codex_review(repo):
    cfg, task = _medium_task(repo, ["gemini"], ran={"gemini"})
    ev = gates.evaluate(repo, task, cfg)
    assert "reviewer codex: unavailable" in ev.blockers
    assert ev.phase != "ready"


def test_gemini_written_passes_with_codex_review(repo):
    cfg, task = _medium_task(repo, ["gemini"], ran={"codex"})
    assert gates.evaluate(repo, task, cfg).blockers == []


def test_both_wrote_needs_both_reviews(repo):
    cfg, task = _medium_task(repo, ["codex", "gemini"], ran={"gemini"})
    assert "reviewer codex: unavailable" in gates.evaluate(repo, task, cfg).blockers


@pytest.mark.parametrize("contributors,expected", [
    ([], None),
    (["codex"], ["gemini"]),
    (["gemini"], ["codex"]),
    (["codex", "gemini"], ["codex", "gemini"]),
])
def test_required_reviewers_medium(contributors, expected):
    assert gates.required_reviewers("medium", contributors) == expected


def test_required_reviewers_high_is_both():
    assert gates.required_reviewers("high", []) == ["codex", "gemini"]


def test_work_gemini_worker_records_gemini(repo, fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_AGY", "write:src/a.py:x=1\n")
    monkeypatch.chdir(repo)
    state.create_task(repo, "t1", "task", ["src/**"], load_config(repo))
    brief = repo / "b.md"
    brief.write_text("implement x=1 in src/a.py\n", encoding="utf-8")

    assert main(["work", "t1", "--brief", str(brief), "--worker", "gemini"]) == 0

    assert (repo / "src/a.py").read_text(encoding="utf-8") == "x=1\n"
    task = state.load_task(repo, "t1")
    assert task.contributors == ["gemini"]
    assert task.events[-1]["worker"] == "gemini"

    args = json.loads((fakes / "agy.args").read_text(encoding="utf-8").splitlines()[-1])
    assert args[args.index("--mode") + 1] == "accept-edits"
    prompt = args[args.index("-p") + 1]
    assert "DISABLED" in prompt
    # agy can't read the git-excluded .delegate/, so the brief goes via a folder outside the repo
    exchange = args[args.index("--add-dir") + 1]
    assert repo.resolve().as_posix() not in Path(exchange).resolve().as_posix()
    assert "brief.md" in prompt and Path(exchange).resolve().as_posix() in prompt
    for flag in ("--dangerously-skip-permissions", "--yolo"):
        assert flag not in args


def test_work_rejects_unknown_worker(repo, monkeypatch):
    monkeypatch.chdir(repo)
    state.create_task(repo, "t1", "task", ["src/**"], load_config(repo))
    brief = repo / "b.md"
    brief.write_text("x\n", encoding="utf-8")
    assert main(["work", "t1", "--brief", str(brief), "--worker", "claude"]) != 0
    assert state.load_task(repo, "t1").contributors == []
