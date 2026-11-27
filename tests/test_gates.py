import json
from pathlib import Path
import sys

import pytest

from delegate import gates, git, state
from delegate.cli import main
from delegate.config import load_config


def test_gates_sequential_all_run(repo, monkeypatch, capsys):
    py = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    toml = (
        "[gates]\n"
        f'a = \'{py} -c "import sys; sys.exit(1)"\'\n'
        f'b = \'{py} -c "import sys; sys.exit(0)"\'\n'
    )
    (repo / "delegate.toml").write_text(toml, encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "test task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["verify", "t1"])
    assert code == 2

    loaded = state.load_task(repo, "t1")
    assert "a" in loaded.gates
    assert "b" in loaded.gates
    assert loaded.gates["a"]["status"] == "fail"
    assert loaded.gates["a"]["exit"] == 1
    assert loaded.gates["b"]["status"] == "pass"
    assert loaded.gates["b"]["exit"] == 0

    out = capsys.readouterr().out
    assert "FAIL a" in out
    assert "PASS b" in out


def test_gate_timeout(repo, monkeypatch, capsys):
    py = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    toml = (
        "[timeouts]\n"
        "gate = 2\n"
        "[gates]\n"
        f'slow = \'{py} -c "import time; time.sleep(30)"\'\n'
    )
    (repo / "delegate.toml").write_text(toml, encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "slow task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["verify", "t1"])
    assert code == 2

    loaded = state.load_task(repo, "t1")
    assert loaded.gates["slow"]["status"] == "timeout"
    assert loaded.gates["slow"]["exit"] is None

    out = capsys.readouterr().out
    assert "TIMEOUT slow" in out


def test_gate_modifying_files_fails(repo, monkeypatch, capsys):
    py = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    toml = f"""[gates]
mutator = '''{py} -c "open('README.md', 'a').write('gate edit')"'''
innocent = '''{py} -c "import sys; sys.exit(0)"'''
"""
    (repo / "delegate.toml").write_text(toml, encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "mutate task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["verify", "t1"])
    assert code == 2

    loaded = state.load_task(repo, "t1")
    assert loaded.gates["mutator"]["status"] == "fail"
    assert loaded.gates["mutator"]["reason"] == "gate modified files"
    assert loaded.gates["innocent"]["status"] == "fail"
    assert loaded.gates["innocent"]["reason"] == "gate modified files"

    out = capsys.readouterr().out
    assert "FAIL mutator" in out
    assert "FAIL innocent" in out


def test_risk_levels(tmp_path):
    cfg = load_config(tmp_path)
    assert gates.compute_risk(["README.md"], cfg) == "low"
    assert gates.compute_risk(["src/a.py"], cfg) == "medium"
    assert gates.compute_risk(["src/auth/login.py"], cfg) == "high"
    assert gates.compute_risk(["delegate.toml", "README.md"], cfg) == "high"
    assert gates.compute_risk(["package-lock.json"], cfg) == "high"
    assert gates.compute_risk([], cfg) == "medium"


def test_verify_all_pass(repo, monkeypatch, capsys):
    py = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    toml = (
        "[gates]\n"
        f'check = \'{py} -c "import sys; sys.exit(0)"\'\n'
    )
    (repo / "delegate.toml").write_text(toml, encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "pass task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["verify", "t1"])
    assert code == 0

    loaded = state.load_task(repo, "t1")
    assert loaded.gates["check"]["status"] == "pass"

    out = capsys.readouterr().out
    assert "PASS check" in out


def test_verify_no_gates(repo, monkeypatch, capsys):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "empty gates task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["verify", "t1"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_verify_failure_prints_log_tail(repo, monkeypatch, capsys):
    py = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    cmd = py + ' -c "import sys; sys.stderr.write(\'fatal failure details\'); sys.exit(1)"'
    toml = "[gates]\nerr = " + json.dumps(cmd) + "\n"  # TOML basic strings accept JSON escapes
    (repo / "delegate.toml").write_text(toml, encoding="utf-8")
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "err task", ["**"], cfg)

    monkeypatch.chdir(repo)
    code = main(["verify", "t1"])
    assert code == 2

    out = capsys.readouterr().out
    assert "FAIL err" in out
    assert "fatal failure details" in out


def test_verify_unknown_task_error(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    code = main(["verify", "unknown"])
    assert code == 1
    err = capsys.readouterr().err
    assert "delegate: task unknown does not exist" in err


def test_verify_busy_task_error(repo, monkeypatch, capsys):
    cfg = load_config(repo)
    task = state.create_task(repo, "t1", "busy task", ["**"], cfg)

    monkeypatch.chdir(repo)
    with state.task_lock(repo, "t1"):
        code = main(["verify", "t1"])
        assert code == 1
    err = capsys.readouterr().err
    assert "delegate: task t1 is busy" in err
