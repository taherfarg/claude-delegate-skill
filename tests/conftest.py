import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo with space"
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=path, check=True)
    (path / "README.md").write_text("# Test project\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Initial commit"],
        cwd=path, check=True,
    )
    return path


@pytest.fixture
def delegate_home(tmp_path, monkeypatch):
    path = tmp_path / "delegate-home"
    path.mkdir()
    monkeypatch.setenv("DELEGATE_HOME", str(path))
    return path


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fake bin"
    bin_dir.mkdir()
    script = Path(__file__).resolve().parent / "fakes" / "fake_cli.py"
    monkeypatch.setenv("FAKE_BIN", str(bin_dir))
    # Keep the launchers ASCII even when Python or the checkout has a Unicode path.
    monkeypatch.setenv("FAKE_CLI_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_CLI_SCRIPT", str(script))
    for name in ("codex", "agy"):
        (bin_dir / f"{name}.count").write_text("0", encoding="ascii")
        if os.name == "nt":
            (bin_dir / f"{name}.cmd").write_text(
                f'@"%FAKE_CLI_PYTHON%" "%FAKE_CLI_SCRIPT%" {name} %*\n', encoding="ascii",
            )
        else:
            launcher = bin_dir / name
            launcher.write_text(
                f'#!/bin/sh\nexec "$FAKE_CLI_PYTHON" "$FAKE_CLI_SCRIPT" {name} "$@"\n', encoding="ascii",
            )
            launcher.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    return bin_dir
