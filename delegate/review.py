import codecs
import json
import re
import shutil
import subprocess
import warnings
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import git, runner, state
from .config import Config, match_glob
from .git import DelegateError
from .runner import RunResult
from .state import Task


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
_ICONS = {"blocker": "🔴", "major": "🟡", "nit": "🟢"}
_SEPARATOR = r"(?:\s*[—–]\s*|\s+-\s+)"
_FINDING = re.compile(
    r"([🔴🟡🟢])\s+(.+?):([0-9]+)" + _SEPARATOR + r"(.+?)" + _SEPARATOR + r"(.+)"
)

REVIEW_EXCLUDE = [
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/poetry.lock",
    "**/uv.lock",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
]


class ReviewRefusal(DelegateError):
    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


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
        if (name == ".env" or name.startswith(".env.")) and lines:
            record(path, lines[0][0], "env-file", name)
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


def _evaluate_reviewer_output(
    result: RunResult,
    out_path: Path,
) -> tuple[str, list[tuple[str, str, int, str, str]]]:
    if result.unavailable:
        return "unavailable", []
    if result.timed_out or result.exit_code != 0:
        return "retry", []
    if not out_path.is_file():
        return "retry", []
    text = out_path.read_text(encoding="utf-8", errors="replace")
    validation = validate_output(text)
    if validation.kind in ("none", "findings"):
        return "ran", validation.findings
    return "retry", []


def _run_reviewer(
    name: str,
    tree: Path,
    taskdir: Path,
    brief_file: Path,
    cfg: Config,
) -> tuple[str, list[tuple[str, str, int, str, str]]]:
    if name == "codex":
        out_path = taskdir / "review_codex.md"
        log_path = taskdir / "review_codex.log"
        timeout = cfg.timeouts.codex_review

        def execute() -> RunResult:
            cmd = [
                runner.resolve("codex"),
                "exec",
                "-C",
                str(tree),
                "-s",
                "read-only",
                "--ephemeral",
                "-o",
                str(out_path),
                "-",
            ]
            return runner.run(
                cmd,
                cwd=tree,
                log_path=log_path,
                timeout=timeout,
                stdin_path=brief_file,
            )
    else:  # "gemini"
        out_path = taskdir / "review_gemini.md"
        log_path = taskdir / "review_gemini.log"
        timeout = cfg.timeouts.agy_review

        def execute() -> RunResult:
            cmd = [
                runner.resolve("agy"),
                "-p",
                f"Read {brief_file.resolve().as_posix()} and do exactly what it says.",
                "--mode",
                "plan",
                "--model",
                cfg.gemini_model,
                "--add-dir",
                str(taskdir),
                "--print-timeout",
                f"{timeout}s",
            ]
            return runner.run(
                cmd,
                cwd=tree,
                log_path=log_path,
                timeout=timeout,
                stdout_path=out_path,
            )

    # Attempt 1
    res1 = execute()
    status1, findings1 = _evaluate_reviewer_output(res1, out_path)
    if status1 != "retry":
        return status1, findings1

    # Retry once
    out_path.unlink(missing_ok=True)
    res2 = execute()
    status2, findings2 = _evaluate_reviewer_output(res2, out_path)
    if status2 == "retry":
        return "not_run", []
    return status2, findings2


def run_review(
    repo: Path,
    task: Task,
    cfg: Config,
    goal: str,
    criteria: str = "",
) -> dict:
    tree = state.task_tree(repo, task)

    # 1. no changes → exit 2 `nothing to review`
    changed = state.changed_files(task, tree)
    if not changed:
        raise ReviewRefusal("nothing to review", exit_code=2)

    # 2. `touched_dirty` → exit 1
    dirty = state.touched_dirty(task, tree)
    if dirty:
        raise DelegateError(f"commit or stash your edits to {dirty[0]} first")

    # 3. 3 rounds already exist → exit 2 `review cap reached (3): ask the human — delegate approve <id> or delegate abandon <id>`
    if len(task.reviews) >= 3:
        raise ReviewRefusal(
            f"review cap reached ({len(task.reviews)}): ask the human — delegate approve {task.id} or delegate abandon {task.id}",
            exit_code=2,
        )

    # 4. secret hits → exit 2, listing `file:line kind masked`
    diff, diff_hash = state.hash_diff(task, tree)
    secret_hits = scan_secrets(diff, cfg.secrets_allow)
    if secret_hits:
        lines = [f"{hit.file}:{hit.line} {hit.kind} {hit.masked}" for hit in secret_hits]
        raise ReviewRefusal("\n".join(lines), exit_code=2)

    taskdir = state.task_dir(repo, task.id)
    taskdir.mkdir(parents=True, exist_ok=True)

    files, untracked = state._changes(task, tree)
    omitted_files = [f for f in files if any(match_glob(f, p) for p in REVIEW_EXCLUDE)]
    included_files = [f for f in files if not any(match_glob(f, p) for p in REVIEW_EXCLUDE)]
    included_untracked = [f for f in untracked if not any(match_glob(f, p) for p in REVIEW_EXCLUDE)]

    if included_files:
        review_diff = git._diff(tree, task.base_commit, included_files, included_untracked)
    else:
        review_diff = ""

    patch_lines = [f"# omitted from review: {f}" for f in omitted_files]
    if review_diff:
        if patch_lines:
            patch_content = "\n".join(patch_lines) + "\n" + review_diff
        else:
            patch_content = review_diff
    else:
        patch_content = "\n".join(patch_lines) + ("\n" if patch_lines else "")

    patch_path = taskdir / "diff.patch"
    patch_path.write_text(patch_content, encoding="utf-8")

    brief_path = taskdir / "review_brief.md"
    minutes = max(1, cfg.timeouts.codex_review // 60)
    brief_content = render_brief(
        goal=goal,
        criteria=criteria,
        diff_path=patch_path,
        tree=tree,
        changed_lines=changed_lines(review_diff),
        minutes=minutes,
    )
    brief_path.write_text(brief_content, encoding="utf-8")

    codex_out = taskdir / "review_codex.md"
    gemini_out = taskdir / "review_gemini.md"
    codex_out.unlink(missing_ok=True)
    gemini_out.unlink(missing_ok=True)

    results = runner.run_parallel([
        lambda: _run_reviewer("codex", tree, taskdir, brief_path, cfg),
        lambda: _run_reviewer("gemini", tree, taskdir, brief_path, cfg),
    ])
    codex_status, codex_raw_findings = results[0]
    gemini_status, gemini_raw_findings = results[1]

    n = len(task.reviews) + 1

    codex_findings = []
    if codex_status == "ran":
        for k, (severity, file, line, problem, why) in enumerate(codex_raw_findings, start=1):
            finding = Finding(
                id=f"codex-{n}-{k}",
                reviewer="codex",
                round=n,
                severity=severity,
                file=file,
                line=line,
                problem=problem,
                why=why,
            )
            codex_findings.append(asdict(finding))

    gemini_findings = []
    if gemini_status == "ran":
        for k, (severity, file, line, problem, why) in enumerate(gemini_raw_findings, start=1):
            finding = Finding(
                id=f"gemini-{n}-{k}",
                reviewer="gemini",
                round=n,
                severity=severity,
                file=file,
                line=line,
                problem=problem,
                why=why,
            )
            gemini_findings.append(asdict(finding))

    round_record = {
        "round": n,
        "diff_hash": diff_hash,
        "reviewers": {
            "codex": {
                "status": codex_status,
                "findings": codex_findings,
            },
            "gemini": {
                "status": gemini_status,
                "findings": gemini_findings,
            },
        },
        "at": datetime.now(timezone.utc).isoformat(),
    }
    task.reviews.append(round_record)
    state.save_task(repo, task)
    return round_record


def triage_finding(
    repo: Path,
    task: Task,
    finding_id: str,
    decision: str,
    reason: str,
    same_as: str | None = None,
) -> dict:
    known_ids = set()
    for r in task.reviews:
        for rev_data in r.get("reviewers", {}).values():
            for f in rev_data.get("findings", []):
                if isinstance(f, dict) and "id" in f:
                    known_ids.add(f["id"])
                elif hasattr(f, "id"):
                    known_ids.add(f.id)

    if finding_id not in known_ids:
        raise DelegateError(f"unknown finding id: {finding_id}")

    if same_as is not None:
        if same_as not in known_ids:
            raise DelegateError(f"unknown finding id: {same_as}")
        if not reason:
            if same_as in task.triage:
                reason = task.triage[same_as].get("reason", "")
            else:
                reason = ""
    else:
        reason = reason or ""

    tree = state.task_tree(repo, task)
    _, diff_hash = state.hash_diff(task, tree)

    entry = {
        "decision": decision,
        "reason": reason,
        "same_as": same_as,
        "diff_hash": diff_hash,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    task.triage[finding_id] = entry
    state.save_task(repo, task)
    return entry
