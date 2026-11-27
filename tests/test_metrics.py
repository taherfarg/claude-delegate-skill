import json
from pathlib import Path

import pytest

from delegate import metrics, state
from delegate.cli import main
from delegate.config import load_config


def test_record_has_no_content(repo, delegate_home):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "secret task", ["**"], cfg)

    secret_text = "SECRET_PROBLEM_TEXT"
    task.reviews = [
        {
            "round": 1,
            "diff_hash": "hash1",
            "reviewers": {
                "codex": {
                    "status": "ran",
                    "findings": [
                        {
                            "id": "codex-1-1",
                            "reviewer": "codex",
                            "round": 1,
                            "severity": "blocker",
                            "file": "src/a.py",
                            "line": 10,
                            "problem": secret_text,
                            "why": "confidential reason",
                        },
                        {
                            "id": "codex-1-2",
                            "reviewer": "codex",
                            "round": 1,
                            "severity": "major",
                            "file": "src/b.py",
                            "line": 20,
                            "problem": "major bug",
                            "why": "logic error",
                        },
                    ],
                },
                "gemini": {
                    "status": "ran",
                    "findings": [
                        {
                            "id": "gemini-1-1",
                            "reviewer": "gemini",
                            "round": 1,
                            "severity": "nit",
                            "file": "src/c.py",
                            "line": 5,
                            "problem": "style nit",
                            "why": "naming",
                        }
                    ],
                },
            },
            "at": "2026-11-27T10:00:00Z",
        }
    ]
    task.triage = {
        "codex-1-1": {"decision": "fix", "reason": "will fix"},
        "codex-1-2": {"decision": "contest", "reason": "disagree"},
    }
    task.gates = {
        "test": {"status": "pass", "exit": 0, "diff_hash": "hash1"},
        "lint": {"status": "fail", "exit": 1, "diff_hash": "hash1"},
    }
    task.events = [
        {"type": "work", "diff_hash": "hash1", "exit": 0, "timed_out": False, "duration": 10.0},
        {"type": "work", "diff_hash": "hash2", "exit": None, "timed_out": True, "duration": 900.0},
    ]
    task.approval = {"diff_hash": "hash1", "override": True}

    class DummyEvaluation:
        risk = "high"

    result = metrics.record(task, cfg, outcome="done", evaluation=DummyEvaluation())

    assert result["project"] == cfg.project
    assert result["task"] == "t1"
    assert result["risk"] == "high"
    assert result["outcome"] == "done"
    assert result["work_runs"] == 2
    assert result["worker_timeouts"] == 1
    assert result["review_rounds"] == 1
    assert result["findings"]["codex"]["blocker"] == 1
    assert result["findings"]["codex"]["major"] == 1
    assert result["findings"]["codex"]["nit"] == 0
    assert result["findings"]["gemini"]["blocker"] == 0
    assert result["findings"]["gemini"]["major"] == 0
    assert result["findings"]["gemini"]["nit"] == 1
    assert result["triage"]["fix"] == 1
    assert result["triage"]["contest"] == 1
    assert result["gates"]["pass"] == 1
    assert result["gates"]["fail"] == 1
    assert result["gates"]["timeout"] == 0
    assert result["reviewer_status"]["codex"]["ran"] == 1
    assert result["reviewer_status"]["gemini"]["ran"] == 1
    assert result["override"] is True

    metrics_file = delegate_home / "metrics.jsonl"
    assert metrics_file.exists()
    content = metrics_file.read_text(encoding="utf-8")
    assert secret_text not in content
    line = content.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed == result


def test_report_empty_and_aggregates(repo, delegate_home, monkeypatch, capsys):
    metrics_file = delegate_home / "metrics.jsonl"
    assert metrics.report(metrics_file) == "no tasks recorded yet"

    cfg = load_config(repo)

    task1 = state.create_task(repo, "t1", "task 1", ["**"], cfg)
    task1.reviews = [
        {
            "round": 1,
            "diff_hash": "h1",
            "reviewers": {
                "codex": {
                    "status": "ran",
                    "findings": [{"id": "c1", "reviewer": "codex", "severity": "blocker"}],
                },
                "gemini": {
                    "status": "not_run",
                    "findings": [],
                },
            },
        }
    ]
    task1.triage = {"c1": {"decision": "contest"}}

    class DummyEval1:
        risk = "low"

    metrics.record(task1, cfg, outcome="done", evaluation=DummyEval1())

    task2 = state.create_task(repo, "t2", "task 2", ["**"], cfg)
    task2.reviews = [
        {
            "round": 1,
            "diff_hash": "h2",
            "reviewers": {
                "codex": {"status": "ran", "findings": []},
                "gemini": {"status": "unavailable", "findings": []},
            },
        },
        {
            "round": 2,
            "diff_hash": "h3",
            "reviewers": {
                "codex": {
                    "status": "ran",
                    "findings": [{"id": "c2", "reviewer": "codex", "severity": "nit"}],
                },
                "gemini": {
                    "status": "ran",
                    "findings": [{"id": "g1", "reviewer": "gemini", "severity": "major"}],
                },
            },
        },
    ]
    task2.triage = {
        "c2": {"decision": "fix"},
        "g1": {"decision": "contest"},
    }

    class DummyEval2:
        risk = "high"

    metrics.record(task2, cfg, outcome="abandon", evaluation=DummyEval2())

    rep = metrics.report(metrics_file)
    assert "no tasks recorded yet" not in rep
    assert "done: 1" in rep
    assert "abandon: 1" in rep
    assert "median wall time" in rep.lower()
    assert "mean rounds" in rep.lower()
    assert "contest rate" in rep.lower()
    assert "findings per reviewer" in rep.lower()
    assert "codex" in rep
    assert "gemini" in rep
    assert "blocker: 1" in rep
    assert "major: 1" in rep
    assert "nit: 1" in rep
    assert "not_run: 1" in rep
    assert "unavailable: 1" in rep

    monkeypatch.chdir(repo)
    capsys.readouterr()
    code = main(["report"])
    assert code == 0
    cli_out = capsys.readouterr().out
    assert "done: 1" in cli_out
    assert "abandon: 1" in cli_out


def test_record_without_evaluation(repo, delegate_home):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "no eval", ["**"], cfg)
    result = metrics.record(task, cfg, outcome="done", evaluation=None)
    assert result["risk"] is None
    assert result["outcome"] == "done"
