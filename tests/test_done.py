import dataclasses
from pathlib import Path
import pytest

from delegate import gates, git, state
from delegate.cli import main
from delegate.config import load_config
from delegate.git import DelegateError


def test_low_risk_needs_only_gates(repo, monkeypatch, capsys):
    (repo / "delegate.toml").write_text('[gates]\ntest = "echo pass"\n', encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "low risk task", ["**"], cfg)

    # README change is low risk
    (repo / "README.md").write_text("# Updated README\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, current_hash = state.hash_diff(task, tree)

    # Record passing gate at current_hash
    task.gates["test"] = {
        "status": "pass",
        "exit": 0,
        "diff_hash": current_hash,
        "log": str(repo / "gate.log"),
        "at": "2026-11-27T00:00:00Z",
    }
    state.save_task(repo, task)

    ev = gates.evaluate(repo, task, cfg)
    assert ev.risk == "low"
    assert ev.blockers == []
    assert ev.phase == "ready"
    assert ev.next_cmd == "delegate done t1"

    monkeypatch.chdir(repo)
    code = main(["done", "t1"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert reloaded.outcome == "done"


def test_stale_gate_after_edit(repo):
    (repo / "delegate.toml").write_text('[gates]\ntest = "echo pass"\n', encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "README.md").write_text("# V1\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, hash1 = state.hash_diff(task, tree)
    task.gates["test"] = {
        "status": "pass",
        "exit": 0,
        "diff_hash": hash1,
        "log": str(repo / "gate.log"),
        "at": "iso",
    }
    state.save_task(repo, task)

    # Edit file after gate run
    (repo / "README.md").write_text("# V2\n", encoding="utf-8")
    ev = gates.evaluate(repo, task, cfg)
    assert "gate test: stale" in ev.blockers
    assert ev.phase == "needs-verify"
    assert ev.next_cmd == "delegate verify t1"


def test_missing_gate_never_passes(repo):
    (repo / "delegate.toml").write_text('[gates]\ntest = "echo pass"\n', encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "README.md").write_text("# V1\n", encoding="utf-8")

    ev = gates.evaluate(repo, task, cfg)
    assert "gate test: missing" in ev.blockers
    assert ev.phase == "needs-verify"
    assert ev.next_cmd == "delegate verify t1"


def test_no_gates_blocks_unless_allowed(repo):
    (repo / "delegate.toml").write_text("", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "README.md").write_text("# V1\n", encoding="utf-8")

    ev = gates.evaluate(repo, task, cfg)
    assert "no gates configured (add [gates] to delegate.toml)" in ev.blockers
    assert ev.phase == "needs-verify"

    # With allow_no_gates = true. Change the setting without editing the repo:
    # editing delegate.toml inside a task is (correctly) high risk.
    cfg2 = dataclasses.replace(cfg, allow_no_gates=True)
    ev2 = gates.evaluate(repo, task, cfg2)
    assert not any("no gates configured" in b for b in ev2.blockers)
    assert ev2.phase == "ready"


def test_medium_codex_written_requires_gemini(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)

    # src/a.py is medium risk
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    task.contributors.append("codex")
    tree = state.task_tree(repo, task)
    _, h = state.hash_diff(task, tree)

    # Only codex ran clean
    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {
            "codex": {"status": "ran", "findings": []},
            "gemini": {"status": "missing", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)

    ev = gates.evaluate(repo, task, cfg)
    assert ev.risk == "medium"
    assert "reviewer gemini: missing" in ev.blockers
    assert ev.phase == "needs-review"
    assert ev.next_cmd == "delegate review t1"


def test_medium_claude_written_either_reviewer(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)

    # Claude wrote it (no codex in contributors)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, h = state.hash_diff(task, tree)

    # Only codex ran clean
    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {
            "codex": {"status": "ran", "findings": []},
            "gemini": {"status": "missing", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)

    ev = gates.evaluate(repo, task, cfg)
    assert ev.risk == "medium"
    assert ev.blockers == []
    assert ev.phase == "ready"
    assert ev.next_cmd == "delegate done t1"


def test_open_major_blocks_until_contested(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, h = state.hash_diff(task, tree)

    finding = {
        "id": "codex-1-1",
        "reviewer": "codex",
        "round": 1,
        "severity": "major",
        "file": "src/a.py",
        "line": 1,
        "problem": "potential defect",
        "why": "why",
    }
    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {
            "codex": {"status": "ran", "findings": [finding]},
            "gemini": {"status": "ran", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)

    # Untriaged -> blocked
    ev = gates.evaluate(repo, task, cfg)
    assert "finding codex-1-1 open" in ev.blockers
    assert ev.phase == "needs-triage"
    assert ev.next_cmd == "delegate triage t1"

    # Triaged fix -> still blocked until new round
    task.triage["codex-1-1"] = {
        "decision": "fix",
        "reason": "will fix later",
        "diff_hash": h,
        "at": "iso",
    }
    state.save_task(repo, task)
    ev_fix = gates.evaluate(repo, task, cfg)
    assert "finding codex-1-1 open" in ev_fix.blockers
    assert ev_fix.phase == "needs-triage"

    # Triaged contest -> ready
    task.triage["codex-1-1"] = {
        "decision": "contest",
        "reason": "intentional design",
        "diff_hash": h,
        "at": "iso",
    }
    state.save_task(repo, task)
    ev_contest = gates.evaluate(repo, task, cfg)
    assert not any("finding codex-1-1 open" in b for b in ev_contest.blockers)
    assert ev_contest.phase == "ready"


def test_high_requires_both_and_approval(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "high risk task", ["**"], cfg)

    # src/auth/login.py is high risk
    (repo / "src" / "auth").mkdir(parents=True)
    (repo / "src" / "auth" / "login.py").write_text("token = 1\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, h = state.hash_diff(task, tree)

    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {
            "codex": {"status": "ran", "findings": []},
            "gemini": {"status": "ran", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)

    # Missing approval -> blocked
    ev = gates.evaluate(repo, task, cfg)
    assert ev.risk == "high"
    assert f"human approval required: run 'delegate approve {task.id}' in your own terminal" in ev.blockers
    assert ev.phase == "needs-approval"
    assert ev.next_cmd == "delegate approve t1"

    # Approve
    gates.approve(repo, task, cfg, isatty=lambda: True, ask=lambda _: "t1")
    reloaded = state.load_task(repo, "t1")
    assert reloaded.approval is not None
    assert reloaded.approval["diff_hash"] == h
    assert reloaded.approval["override"] is False

    ev_approved = gates.evaluate(repo, reloaded, cfg)
    assert ev_approved.blockers == []
    assert ev_approved.phase == "ready"


def test_approval_goes_stale_on_edit(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)

    (repo / "src" / "auth").mkdir(parents=True)
    (repo / "src" / "auth" / "login.py").write_text("token = 1\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, h1 = state.hash_diff(task, tree)

    task.reviews.append({
        "round": 1,
        "diff_hash": h1,
        "reviewers": {
            "codex": {"status": "ran", "findings": []},
            "gemini": {"status": "ran", "findings": []},
        },
        "at": "iso",
    })
    gates.approve(repo, task, cfg, isatty=lambda: True, ask=lambda _: "t1")

    # Edit after approval
    (repo / "src" / "auth" / "login.py").write_text("token = 2\n", encoding="utf-8")
    reloaded = state.load_task(repo, "t1")
    ev = gates.evaluate(repo, reloaded, cfg)
    assert f"human approval required: run 'delegate approve {task.id}' in your own terminal" in ev.blockers


def test_approve_refuses_without_tty(repo):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    with pytest.raises(DelegateError, match="approve must be run by a human in an interactive terminal"):
        gates.approve(repo, task, cfg, isatty=lambda: False)


def test_approve_overrides_unavailable_reviewer(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)

    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    task.contributors.append("codex")
    tree = state.task_tree(repo, task)
    _, h = state.hash_diff(task, tree)

    # Gemini unavailable
    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {
            "codex": {"status": "ran", "findings": []},
            "gemini": {"status": "unavailable", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)

    ev_before = gates.evaluate(repo, task, cfg)
    assert "reviewer gemini: unavailable" in ev_before.blockers

    # Approve
    gates.approve(repo, task, cfg, isatty=lambda: True, ask=lambda _: "t1")
    reloaded = state.load_task(repo, "t1")
    assert reloaded.approval is not None
    assert reloaded.approval["override"] is True

    ev_after = gates.evaluate(repo, reloaded, cfg)
    assert ev_after.blockers == []
    assert ev_after.phase == "ready"


def test_out_of_scope_blocks(repo):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["src/**"], cfg)

    (repo / "extra.txt").write_text("outside\n", encoding="utf-8")
    ev = gates.evaluate(repo, task, cfg)
    assert "out of scope: extra.txt" in ev.blockers


def test_status_next_cmd(repo, monkeypatch, capsys):
    (repo / "delegate.toml").write_text('[gates]\ntest = "echo pass"\n', encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    monkeypatch.chdir(repo)

    # 1. no-changes
    ev = gates.evaluate(repo, task, cfg)
    assert ev.phase == "no-changes"
    assert ev.next_cmd == "delegate work t1"
    main(["status", "t1"])
    out = capsys.readouterr().out
    assert "delegate work t1" in out

    # 2. needs-verify (after edit, gate not run)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    ev = gates.evaluate(repo, task, cfg)
    assert ev.phase == "needs-verify"
    assert ev.next_cmd == "delegate verify t1"
    main(["status", "t1"])
    out = capsys.readouterr().out
    assert "delegate verify t1" in out

    # 3. needs-review (gate passed at current hash, review not run)
    tree = state.task_tree(repo, task)
    _, h = state.hash_diff(task, tree)
    task.gates["test"] = {
        "status": "pass",
        "exit": 0,
        "diff_hash": h,
        "log": str(repo / "gate.log"),
        "at": "iso",
    }
    state.save_task(repo, task)
    ev = gates.evaluate(repo, task, cfg)
    assert ev.phase == "needs-review"
    assert ev.next_cmd == "delegate review t1"
    main(["status", "t1"])
    out = capsys.readouterr().out
    assert "delegate review t1" in out

    # 4. needs-triage (review ran, has open finding)
    task.reviews.append({
        "round": 1,
        "diff_hash": h,
        "reviewers": {
            "codex": {
                "status": "ran",
                "findings": [
                    {
                        "id": "codex-1-1",
                        "severity": "major",
                        "file": "src/a.py",
                        "line": 1,
                        "problem": "prob",
                        "why": "why",
                    }
                ],
            },
            "gemini": {"status": "ran", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)
    ev = gates.evaluate(repo, task, cfg)
    assert ev.phase == "needs-triage"
    assert ev.next_cmd == "delegate triage t1"
    main(["status", "t1"])
    out = capsys.readouterr().out
    assert "delegate triage t1" in out

    # 5. needs-approval (high risk task, finding contested, missing approval)
    task.triage["codex-1-1"] = {
        "decision": "contest",
        "reason": "accepted",
        "diff_hash": h,
        "at": "iso",
    }
    (repo / "src" / "auth").mkdir()
    (repo / "src" / "auth" / "login.py").write_text("secret = 1\n", encoding="utf-8")
    tree = state.task_tree(repo, task)
    _, h2 = state.hash_diff(task, tree)
    task.gates["test"]["diff_hash"] = h2
    task.reviews.append({
        "round": 2,
        "diff_hash": h2,
        "reviewers": {
            "codex": {"status": "ran", "findings": []},
            "gemini": {"status": "ran", "findings": []},
        },
        "at": "iso",
    })
    state.save_task(repo, task)
    ev = gates.evaluate(repo, task, cfg)
    assert ev.phase == "needs-approval"
    assert ev.next_cmd == "delegate approve t1"
    main(["status", "t1"])
    out = capsys.readouterr().out
    assert "delegate approve t1" in out

    # 6. ready (after approval)
    gates.approve(repo, task, cfg, isatty=lambda: True, ask=lambda _: "t1")
    reloaded = state.load_task(repo, "t1")
    ev = gates.evaluate(repo, reloaded, cfg)
    assert ev.phase == "ready"
    assert ev.next_cmd == "delegate done t1"
    main(["status", "t1"])
    out = capsys.readouterr().out
    assert "delegate done t1" in out


def test_done_marks_outcome_and_prints_merge_for_worktree(repo, monkeypatch, capsys):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "wt task", ["**"], cfg, worktree=True)
    tree = state.task_tree(repo, task)
    assert tree != repo

    # Low-risk change in worktree
    (tree / "README.md").write_text("# Updated in worktree\n", encoding="utf-8")

    monkeypatch.chdir(repo)
    code = main(["done", "t1"])
    assert code == 0

    out = capsys.readouterr().out
    assert "git merge delegate/t1" in out
    assert f"git worktree remove {task.worktree}" in out

    reloaded = state.load_task(repo, "t1")
    assert reloaded.outcome == "done"


def test_abandon_marks_outcome(repo, monkeypatch):
    cfg = load_config(repo)
    state.create_task(repo, "t1", "abandon task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["abandon", "t1"])
    assert code == 0

    reloaded = state.load_task(repo, "t1")
    assert reloaded.outcome == "abandoned"


def test_status_all_open_tasks_only(repo, monkeypatch, capsys):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    state.create_task(repo, "t1", "open task", ["**"], cfg)
    t2 = state.create_task(repo, "t2", "done task", ["**"], cfg)
    t2.outcome = "done"
    state.save_task(repo, t2)

    monkeypatch.chdir(repo)
    code = main(["status"])
    assert code == 0

    out = capsys.readouterr().out
    assert "t1  no-changes  medium" in out
    assert "t2" not in out


def test_done_blocked_exits_2_and_prints_blockers(repo, monkeypatch, capsys):
    (repo / "delegate.toml").write_text('[gates]\ncheck = "exit 1"\n', encoding="utf-8")
    cfg = load_config(repo)
    state.create_task(repo, "t1", "blocked task", ["**"], cfg)
    (repo / "README.md").write_text("# edit\n", encoding="utf-8")

    monkeypatch.chdir(repo)
    code = main(["done", "t1"])
    assert code == 2

    out = capsys.readouterr().out
    assert "gate check: missing" in out
