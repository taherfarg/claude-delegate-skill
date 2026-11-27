import json
from pathlib import Path

import pytest

from delegate import setup
from delegate.cli import main
from delegate.config import load_config
from delegate.git import DelegateError


def test_doctor_with_fakes_all_pass(repo, fakes, monkeypatch, capsys):
    checks = setup.doctor()
    assert all(ok for _, ok, _ in checks)
    names = [name for name, _, _ in checks]
    assert names == ["codex", "agy", "git", "python"]

    monkeypatch.chdir(repo)
    code = main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "PASS codex" in out
    assert "PASS agy" in out
    assert "PASS git" in out
    assert "PASS python" in out


def test_doctor_missing_model_fails(repo, fakes, monkeypatch, capsys):
    checks = setup.doctor(model="missing-model-xyz")
    agy_check = [c for c in checks if c[0] == "agy"][0]
    assert agy_check[1] is False

    (repo / "delegate.toml").write_text('gemini_model = "missing-model-xyz"\n', encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(["doctor"])
    assert code == 2
    out = capsys.readouterr().out
    assert "FAIL agy" in out


def test_init_detects_npm_scripts(repo, monkeypatch):
    pkg_json = repo / "package.json"
    pkg_json.write_text(
        json.dumps({
            "name": "example-pkg",
            "scripts": {
                "lint": "eslint .",
                "typecheck": "tsc --noEmit",
                "test": "jest",
            },
        }),
        encoding="utf-8",
    )

    created_path = setup.init(repo)
    assert created_path == repo / "delegate.toml"
    assert created_path.is_file()

    cfg = load_config(repo)
    assert cfg.gates["lint"] == "npm run lint"
    assert cfg.gates["typecheck"] == "npm run typecheck"
    assert cfg.gates["test"] == "npm run test"

    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".delegate/" in exclude


def test_init_detects_pytest(repo, monkeypatch):
    (repo / "tests").mkdir(exist_ok=True)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        "[tool.ruff]\nline-length = 88\n",
        encoding="utf-8",
    )

    setup.init(repo)
    cfg = load_config(repo)
    assert cfg.gates["test"] == "python -m pytest -q"
    assert cfg.gates["ruff"] == "ruff check ."


def test_init_refuses_overwrite(repo, monkeypatch, capsys):
    (repo / "delegate.toml").write_text('[gates]\n', encoding="utf-8")

    with pytest.raises(DelegateError, match="already exists"):
        setup.init(repo)

    monkeypatch.chdir(repo)
    code = main(["init"])
    assert code == 1
    err = capsys.readouterr().err
    assert "delegate:" in err
    assert "already exists" in err
