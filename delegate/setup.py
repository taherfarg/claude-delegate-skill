import json
import os
import sys
import tempfile
from pathlib import Path

from . import git, runner
from .config import Config, load_config
from .git import DelegateError, main_root
from .state import _exclude_state


def doctor(cfg: Config | None = None, model: str | None = None) -> list[tuple[str, bool, str]]:
    target_model = model
    if target_model is None and cfg is not None:
        target_model = cfg.gemini_model
    if target_model is None:
        try:
            target_model = load_config(main_root(Path.cwd())).gemini_model
        except Exception:
            target_model = "gemini-3.8-flash-high"

    # 1. codex login status contains ChatGPT
    try:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "codex.log"
            res = runner.run(["codex", "login", "status"], Path.cwd(), log_path, timeout=15)
            output = log_path.read_text(encoding="utf-8", errors="replace").strip()
            codex_ok = (res.exit_code == 0) and ("ChatGPT" in output)
            codex_detail = output.splitlines()[0] if output else ("logged in" if codex_ok else "not logged in")
    except Exception as exc:
        codex_ok = False
        codex_detail = str(exc)

    # 2. agy models lists the configured model
    try:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "agy.log"
            res = runner.run(["agy", "models"], Path.cwd(), log_path, timeout=15)
            output = log_path.read_text(encoding="utf-8", errors="replace").strip()
            models = output.split()
            agy_ok = (res.exit_code == 0) and (target_model in models or target_model in output)
            if agy_ok:
                agy_detail = target_model
            else:
                agy_detail = f"{target_model} not found in models" if output else "no models returned"
    except Exception as exc:
        agy_ok = False
        agy_detail = str(exc)

    # 3. git --version
    try:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "git.log"
            res = runner.run(["git", "--version"], Path.cwd(), log_path, timeout=10)
            output = log_path.read_text(encoding="utf-8", errors="replace").strip()
            git_ok = (res.exit_code == 0) and ("git version" in output.lower())
            git_detail = output.splitlines()[0] if output else ("git present" if git_ok else "git check failed")
    except Exception as exc:
        git_ok = False
        git_detail = str(exc)

    # 4. Python >= 3.11
    py_ok = sys.version_info >= (3, 11)
    py_detail = f"Python {sys.version.split()[0]}"

    return [
        ("codex", codex_ok, codex_detail),
        ("agy", agy_ok, agy_detail),
        ("git", git_ok, git_detail),
        ("python", py_ok, py_detail),
    ]


def init(repo: Path) -> Path:
    target = repo / "delegate.toml"
    if target.exists():
        raise DelegateError(f"{target.name} already exists")

    gates: dict[str, str] = {}

    pkg_json = repo / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            for name in ("lint", "typecheck", "test"):
                if name in scripts and isinstance(scripts[name], str):
                    gates[name] = f"npm run {name}"
        except Exception:
            pass

    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "ruff" in content:
                gates["ruff"] = "ruff check ."
        except Exception:
            pass

    has_pytest_config = any(
        (repo / fname).is_file()
        for fname in ("pytest.ini", "setup.cfg", "tox.ini")
    )
    if not has_pytest_config and pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "pytest" in content or "tool.pytest" in content:
                has_pytest_config = True
        except Exception:
            pass
    has_tests_dir = (repo / "tests").is_dir()

    if (has_tests_dir or has_pytest_config) and "test" not in gates:
        gates["test"] = "python -m pytest -q"

    lines = ["[gates]"]
    if gates:
        for name, cmd in gates.items():
            lines.append(f'{name} = "{cmd}"')
    else:
        lines.append('# Add gate commands here, e.g. test = "python -m pytest -q"')
    lines.append("")

    target.write_text("\n".join(lines), encoding="utf-8")

    try:
        root = main_root(repo)
    except Exception:
        root = repo
    _exclude_state(root)

    return target
