import subprocess

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
