"""Regression tests for Claude's independent review of the Gemini-written engine code."""
import json

from delegate import gates, review, state, work
from delegate.config import load_config


def _medium_task(repo, contributors=("codex",)):
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    task.contributors.extend(contributors)
    state.save_task(repo, task)
    return cfg, task


def test_approval_does_not_replace_a_missing_review(repo):
    cfg, task = _medium_task(repo)
    gates.approve(repo, task, cfg, isatty=lambda: True, ask=lambda _: "t1")
    ev = gates.evaluate(repo, state.load_task(repo, "t1"), cfg)
    assert "review missing" in ev.blockers
    assert ev.phase == "needs-review"


def test_approval_still_overrides_unavailable_reviewer_after_a_round(repo):
    cfg, task = _medium_task(repo)
    _, h = state.hash_diff(task, state.task_tree(repo, task))
    task.reviews.append({"round": 1, "diff_hash": h, "at": "iso", "reviewers": {
        "codex": {"status": "ran", "findings": []},
        "gemini": {"status": "unavailable", "findings": []}}})
    state.save_task(repo, task)
    gates.approve(repo, task, cfg, isatty=lambda: True, ask=lambda _: "t1")
    assert gates.evaluate(repo, state.load_task(repo, "t1"), cfg).blockers == []


def test_approve_warns_when_task_changes_gate_rules(repo, capsys):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "delegate.toml").write_text("allow_no_gates = true\n", encoding="utf-8")
    gates.approve(repo, task, load_config(repo), isatty=lambda: True, ask=lambda _: "t1")
    out = capsys.readouterr().out
    assert "WARNING" in out and "delegate.toml" in out


def test_rounds_where_no_reviewer_ran_do_not_use_up_the_cap(repo, fakes, monkeypatch):
    cfg, task = _medium_task(repo)
    for n in (1, 2, 3):
        task.reviews.append({"round": n, "diff_hash": "x", "at": "iso", "reviewers": {
            "codex": {"status": "unavailable", "findings": []},
            "gemini": {"status": "not_run", "findings": []}}})
    state.save_task(repo, task)
    monkeypatch.setenv("FAKE_CODEX", "none")
    monkeypatch.setenv("FAKE_AGY", "none")
    record = review.run_review(repo, task, cfg, goal="g")
    assert record["reviewers"]["gemini"]["status"] == "ran"


def test_worker_cannot_forge_other_task_state(repo, fakes, monkeypatch, tmp_path):
    cfg = load_config(repo)
    other = state.create_task(repo, "other", "other", ["**"], cfg)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    other_state = state.task_dir(repo, "other") / "state.json"
    original = other_state.read_bytes()
    forged = json.loads(original)
    forged["approval"] = {"diff_hash": "x", "at": "iso", "override": True}
    rel = other_state.relative_to(repo).as_posix()
    monkeypatch.setenv("FAKE_CODEX", f"write:{rel}:{json.dumps(forged)}")
    brief = tmp_path / "brief.md"
    brief.write_text("do it", encoding="utf-8")
    work.run_worker(repo, task, cfg, brief)
    assert other_state.read_bytes() == original
    assert any(e.get("type") == "tamper" for e in state.load_task(repo, "t1").events)


def test_diff_stat_has_no_phantom_crlf_changes(repo):
    import subprocess
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=repo, check=True)
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "task", ["**"], cfg)
    (repo / "README.md").write_bytes((repo / "README.md").read_bytes().replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n"))
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    stat = work.diff_stat(task, state.task_tree(repo, task))
    assert "b.py" in stat and "README.md" not in stat
