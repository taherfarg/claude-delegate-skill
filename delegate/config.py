import tomllib
from dataclasses import dataclass, fields
from fnmatch import fnmatch
from pathlib import Path, PurePath


DEFAULT_RISK_LOW = ["**/*.md", "**/*.txt", "**/*.css", "**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.gif", "**/*.svg", "**/*.webp", "docs/**"]
DEFAULT_RISK_HIGH = ["**/auth/**", "**/*auth*", "**/migrations/**", "**/*.sql", "**/payment*/**", "**/*payment*", "**/*billing*", ".github/**", "package.json", "**/package.json", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "pyproject.toml", "**/requirements*.txt", "**/poetry.lock", "**/uv.lock", "**/go.mod", "**/Cargo.toml", "**/pytest.ini", "**/setup.cfg", "**/tox.ini", "**/conftest.py", "**/.eslintrc*", "**/eslint.config.*", "**/tsconfig*.json", "**/vitest.config.*", "**/jest.config.*", "**/playwright.config.*", "delegate.toml"]


class ConfigError(Exception):
    """The project configuration could not be read or has an invalid value."""


@dataclass
class Timeouts:
    worker: int = 900
    codex_review: int = 300
    agy_review: int = 480
    gate: int = 600


@dataclass
class Config:
    project: str
    gates: dict[str, str]
    allow_no_gates: bool
    risk_low: list[str]
    risk_high: list[str]
    secrets_allow: list[str]
    worktree_setup: str | None
    gemini_model: str
    timeouts: Timeouts


def _option(table: dict, key: str, kind: type, default=None):
    if key not in table:
        return default
    value = table[key]
    if type(value) is not kind:
        raise ConfigError(f"delegate.toml: {key} must have type {kind.__name__}")
    return value


def _patterns(table: dict, key: str) -> list[str]:
    patterns = _option(table, key, list, [])
    if not all(isinstance(pattern, str) for pattern in patterns):
        raise ConfigError(f"delegate.toml: {key} must contain only strings")
    return patterns


def load_config(repo_root: Path) -> Config:
    path = repo_root / "delegate.toml"
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    gates = _option(data, "gates", dict, {})
    if not all(isinstance(command, str) for command in gates.values()):
        raise ConfigError("delegate.toml: gate commands must be strings")
    risk = _option(data, "risk", dict, {})
    secrets = _option(data, "secrets", dict, {})
    worktree = _option(data, "worktree", dict, {})
    timeout_values = _option(data, "timeouts", dict, {})
    timeouts = Timeouts(**{
        field.name: _option(timeout_values, field.name, int, field.default)
        for field in fields(Timeouts)
    })

    return Config(
        project=_option(data, "project", str, repo_root.resolve().name),
        gates=gates,
        allow_no_gates=_option(data, "allow_no_gates", bool, False),
        risk_low=DEFAULT_RISK_LOW + _patterns(risk, "low"),
        risk_high=DEFAULT_RISK_HIGH + _patterns(risk, "high"),
        secrets_allow=_patterns(secrets, "allow"),
        worktree_setup=_option(worktree, "setup", str),
        gemini_model=_option(data, "gemini_model", str, "gemini-3.8-flash-high"),
        timeouts=timeouts,
    )


def match_glob(path: str, pattern: str) -> bool:
    parts = PurePath(path).parts
    # Each position counts the path components matched by the pattern so far.
    positions = {0}
    for component in PurePath(pattern).parts:
        if component == "**":
            positions = set(range(min(positions), len(parts) + 1))
        else:
            positions = {
                pos + 1 for pos in positions
                if pos < len(parts) and fnmatch(parts[pos], component)
            }
        if not positions:
            return False
    return len(parts) in positions
