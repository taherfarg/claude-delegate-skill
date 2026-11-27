import codecs
import json
import re
import shutil
import subprocess
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import match_glob


@dataclass
class Finding:
    id: str
    reviewer: str
    round: int
    severity: str
    file: str
    line: int
    problem: str
    why: str


@dataclass
class Validation:
    kind: str
    findings: list[tuple[str, str, int, str, str]]


@dataclass
class SecretHit:
    file: str
    line: int
    kind: str
    masked: str


_SEVERITIES = {"🔴": "blocker", "🟡": "major", "🟢": "nit"}
_SEPARATOR = r"(?:\s*[—–]\s*|\s+-\s+)"
_FINDING = re.compile(
    r"([🔴🟡🟢])\s+(.+?):([0-9]+)" + _SEPARATOR + r"(.+?)" + _SEPARATOR + r"(.+)"
)


def validate_output(text: str) -> Validation:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and re.fullmatch(r"```[\w.+-]*", lines[0]) and lines[-1] == "```":
        lines = lines[1:-1]
    if lines == ["NO FINDINGS"]:
        return Validation("none", [])
    if len(lines) == 1 and lines[0].startswith("COULD NOT READ "):
        return Validation("could_not_read", [])
    if not lines:
        return Validation("invalid", [])

    findings = []
    for line in lines:
        match = _FINDING.fullmatch(line)
        if not match:
            return Validation("invalid", [])
        icon, file, number, problem, why = match.groups()
        if not (file.strip() and problem.strip() and why.strip()):
            return Validation("invalid", [])
        try:
            line_number = int(number)
        except ValueError:
            return Validation("invalid", [])
        findings.append((_SEVERITIES[icon], file.strip(), line_number, problem.strip(), why.strip()))
    return Validation("findings", findings)


_HUNK = re.compile(r"@@ -[0-9]+(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@")
# Spaces can be unquoted. For an unchanged name, the two header paths agree
# after their prefixes. Renames and copies have separate, unambiguous headers.
_GIT_FILE = re.compile(
    r'diff --git (?P<old>(?P<quote>"?)a/(?P<path>.+)(?P=quote)) '
    r'(?P=quote)b/(?P=path)(?P=quote)'
)
_RENAME_OR_COPY = re.compile(r"(?:rename|copy) (?:from|to) (.+)")


def _diff_path(field: str, prefix: str) -> str:
    path = field.partition("\t")[0]
    if path.startswith('"') and path.endswith('"'):
        # Git C-quotes special characters and may print UTF-8 bytes as octal.
        raw, _ = codecs.escape_decode(path[1:-1].encode("utf-8", "surrogateescape"))
        path = raw.decode("utf-8", "surrogateescape")
    return path.removeprefix(prefix)


def _patch_additions(diff: str) -> dict[str, list[tuple[int, str]]]:
    """Keep every file, including empty files, and its numbered added lines."""
    files: dict[str, list[tuple[int, str]]] = {}
    path = "<diff>"
    number = 1
    old_left = new_left = 0
    for raw in diff.split("\n"):
        line = raw.removesuffix("\r")
        if line.startswith("diff --git "):
            old_left = new_left = 0
            number = 1
            path = "<diff>"
            if header := _GIT_FILE.fullmatch(line):
                path = _diff_path(header["old"], "a/")
                files.setdefault(path, [])
            continue
        if rename := _RENAME_OR_COPY.fullmatch(line):
            path = _diff_path(rename[1], "")
            files.setdefault(path, [])
            continue
        if hunk := _HUNK.match(line):
            old_left = int(hunk[1] or 1)
            number = int(hunk[2])
            new_left = int(hunk[3] or 1)
            continue
        # Inside a hunk, even a line beginning with "+++ " can be added code.
        if old_left <= 0 and new_left <= 0 and line.startswith(("--- ", "+++ ")):
            new_side = line.startswith("+++ ")
            name = _diff_path(line[4:], "b/" if new_side else "a/")
            if name != "/dev/null":
                files.setdefault(name, [])
            if new_side:
                path, number = name, 1
            continue
        if line.startswith("+"):
            files.setdefault(path, []).append((number, line[1:]))
        if line.startswith(("+", " ")):
            number += 1
            new_left -= 1
        if line.startswith(("-", " ")):
            old_left -= 1
    return files


def changed_lines(diff: str) -> dict[str, list[int]]:
    return {
        path: [number for number, _ in additions]
        for path, additions in _patch_additions(diff).items() if additions
    }


_SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("credential", re.compile(
        r"""\b\w*(?:key|token|secret|password)["']?\s*[:=]\s*"""
        r"""(?:"([^"]{16,})"|'([^']{16,})')""", re.IGNORECASE,
    )),
)


def _gitleaks_hits(
    executable: str, additions: list[tuple[str, int, str]],
) -> Iterator[tuple[str, int, str, str]]:
    payload = "\n".join(text for _, _, text in additions) + "\n"
    try:
        # --pipe reads stdin. Adding --no-git can select a directory scan instead.
        result = subprocess.run(
            [executable, "detect", "--pipe", "--report-format", "json",
             "--report-path", "-", "--exit-code", "0"],
            input=payload.encode("utf-8", "surrogateescape"), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True, timeout=30,
        )
        report = json.loads(result.stdout)
        if report is None:
            return
        if not isinstance(report, list):
            raise ValueError("invalid gitleaks report")
        for item in report:
            start = item["StartLine"]
            value = item["Secret"]
            if not (isinstance(start, int) and 1 <= start <= len(additions)
                    and isinstance(value, str) and value):
                raise ValueError("invalid gitleaks finding")
            path, number, _ = additions[start - 1]
            yield path, number, f"gitleaks:{item['RuleID']}", value
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        warnings.warn("gitleaks scan failed. Using built-in secret checks.", RuntimeWarning, stacklevel=2)


def scan_secrets(diff: str, allow: list[str]) -> list[SecretHit]:
    hits: dict[tuple[str, int, str], SecretHit] = {}
    executable = shutil.which("gitleaks")
    additions = []

    def record(path: str, number: int, kind: str, value: str) -> None:
        hits.setdefault((path, number, value), SecretHit(path, number, kind, value[:4] + "…"))

    for path, lines in _patch_additions(diff).items():
        if any(match_glob(path, pattern) for pattern in allow):
            continue
        name = PurePosixPath(path).name
        if name == ".env" or name.startswith(".env."):
            record(path, 1, "env-file", name)
        for number, text in lines:
            if executable:
                additions.append((path, number, text))
            for kind, pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    # Assignment patterns capture the value inside its quotes.
                    record(path, number, kind, match.group(match.lastindex or 0))
    if executable and additions:
        for hit in _gitleaks_hits(executable, additions):
            record(*hit)
    return list(hits.values())


def _line_ranges(numbers: list[int]) -> str:
    ranges: list[tuple[int, int]] = []
    for number in sorted(set(numbers)):
        if ranges and ranges[-1][1] + 1 == number:
            ranges[-1] = (ranges[-1][0], number)
        else:
            ranges.append((number, number))
    return ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in ranges)


def render_brief(
    goal: str, criteria: str, diff_path: Path, tree: Path,
    changed_lines: dict[str, list[int]], minutes: int,
) -> str:
    locations = "\n".join(
        f"{path}:{_line_ranges(numbers)}" for path, numbers in sorted(changed_lines.items())
        if numbers
    )
    return (
        f"Goal\n{goal}\n\nAcceptance criteria\n{criteria}\n\n"
        f"Read the diff at {diff_path.resolve().as_posix()}\n"
        f"Working tree: {tree.resolve().as_posix()}\n\n"
        f"Recently changed\n{locations or '(no added lines)'}\n\n"
        "Do NOT run shell/terminal commands (they are denied); read files only with your file-reading tools.\n"
        f"Spend at most ~{minutes} minutes; do not build test harnesses.\n\n"
        "Output one finding per line. Use 🔴 for a blocker, 🟡 for a major issue, and 🟢 for a nit.\n"
        "<🔴|🟡|🟢> <file>:<line> — <problem> — <why>\n"
        "If you could not read the change, output exactly: COULD NOT READ <path>\n"
        "If you find nothing, output exactly: NO FINDINGS\n"
    )
