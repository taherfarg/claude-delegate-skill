# delegate v2 Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdlib-only Python CLI, `delegate`, that Claude calls to run Codex/Gemini workers and reviewers with timeouts, validated output and secret scanning, and that refuses `done` unless evidence-based, risk-tiered gates hold on the current code.

**Architecture:** Task state is a JSON file per task under the repo's `.delegate/`; everything else (risk, freshness, next step) is **derived** from that evidence plus the current scoped diff and its sha256 (`diff_hash`). External CLIs run through one runner that enforces timeouts with process-tree kill. Tests replace `codex`/`agy` with fake executables on PATH.

**Tech Stack:** Python ≥ 3.11 stdlib (`argparse`, `tomllib`, `subprocess`, `hashlib`, `concurrent.futures`, `dataclasses`, `json`, `re`); pytest (dev only); git; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-27-delegate-v2-design.md`

## Global Constraints

- Runtime: Python ≥ 3.11, **standard library only**. pytest is a dev extra.
- Must work on Windows 11 (primary), Linux, macOS; paths with spaces and non-ASCII names.
- Never pass `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`, `--yolo`, `-s danger-full-access`.
- Codex worker: `codex exec -C <tree> -s workspace-write -o <result> -` (brief on stdin). Codex review: `codex exec -C <tree> -s read-only --ephemeral -o <out> -`. Gemini: `agy -p "Read <brief> and do exactly what it says." --mode plan --model <model> --add-dir <taskdir> --print-timeout <N>s`, cwd = tree, stdout = output file.
- Default model `gemini-3.8-flash-high`. Default timeouts (s): worker 900, codex_review 300, agy_review 480, gate 600.
- Every agy brief contains: `Do NOT run shell/terminal commands (they are denied); read files only with your file-reading tools.`
- State dir: `<main repo root>/.delegate/`, added to `.git/info/exclude`, never to `.gitignore`. Metrics: `~/.delegate/metrics.jsonl` (`DELEGATE_HOME` env overrides `~/.delegate`).
- Review output format: `NO FINDINGS` | `COULD NOT READ <path>` | lines `<🔴|🟡|🟢> <file>:<line> — <problem> — <why>` (`—`, `–`, or ` - ` accepted as separator).
- Review round cap: 3. Gates run sequentially, never fail-fast.
- Exit codes: 0 ok, 1 usage/environment error (`delegate: <reason>` on stderr), 2 refused by a gate/rule (blockers listed one per line).
- Risk lists in `delegate.toml` **extend** the defaults; they can never remove a default pattern.

## Review Focus

1. **Paths with spaces / Arabic names** (owner has `MALTI RAG SYESTEM`, Arabic PDFs): every command must work → test in Task 2 with repo dir `"repo with space"` and file `"مجلد/ملف.py"`.
2. **CRLF / `core.autocrlf=true` on Windows** making phantom diffs → `diff_hash` must be identical across two calls with no edits → test in Task 2.
3. **No commits / not a git repo** → `delegate: not a git repository` / `delegate: repository has no commits` (exit 1), not a traceback → test in Task 2.
4. **Lockfiles and minified files bloating review diffs** until reviewers time out → excluded from the *review* diff but still in the *hash* diff and in risk → test in Task 6.
5. **Two delegate commands on one task at once** (parallel Bash calls) corrupting state → per-task lock file; second command exits 1 `delegate: task <id> is busy` → test in Task 2.

---

### Task 1: Package skeleton and config

**Files:**
- Create: `pyproject.toml`, `delegate/__init__.py`, `delegate/__main__.py`, `delegate/cli.py`, `delegate/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `delegate.__version__ = "2.0.0"`
  - `cli.main(argv: list[str] | None = None) -> int` (argparse with subcommands; this task adds `--version` only)
  - `config.Timeouts(worker=900, codex_review=300, agy_review=480, gate=600)` dataclass
  - `config.Config` dataclass: `project: str, gates: dict[str, str], allow_no_gates: bool, risk_low: list[str], risk_high: list[str], secrets_allow: list[str], worktree_setup: str | None, gemini_model: str, timeouts: Timeouts`
  - `config.load_config(repo_root: Path) -> Config` (reads `delegate.toml` if present; `project` defaults to folder name)
  - `config.match_glob(path: str, pattern: str) -> bool` (`**` = any number of dirs, `*` = within one segment, posix paths, case-insensitive on Windows)
  - `config.DEFAULT_RISK_LOW`, `config.DEFAULT_RISK_HIGH` lists

- [ ] **Step 1: Write failing tests** in `tests/test_config.py`:
  - `test_defaults_without_file`: `load_config(tmp)` → `gates == {}`, `gemini_model == "gemini-3.8-flash-high"`, `timeouts.worker == 900`, `project == tmp.name`.
  - `test_toml_values_and_risk_extends_defaults`: toml with `[gates] test="pytest -q"`, `[risk] high=["src/billing/**"]`, `[timeouts] gate=30` → gates set, `"src/billing/**" in risk_high`, every `DEFAULT_RISK_HIGH` item still in `risk_high`, `timeouts.gate == 30`, `timeouts.worker == 900`.
  - `test_match_glob`: `match_glob("a/b/auth/x.py", "**/auth/**")`, `match_glob("README.md", "**/*.md")`, `not match_glob("src/a.py", "*.py")`, `match_glob("db/migrations/001.sql", "**/migrations/**")`.
  - `test_invalid_toml_is_clean_error`: broken toml → raises `ConfigError` whose message contains `delegate.toml`.
  - `test_version`: `main(["--version"])` prints `2.0.0`.
- [ ] **Step 2:** `python -m pytest tests/test_config.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement.** Defaults, pinned by the spec:
  - `DEFAULT_RISK_LOW = ["**/*.md", "**/*.txt", "**/*.css", "**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.gif", "**/*.svg", "**/*.webp", "docs/**"]`
  - `DEFAULT_RISK_HIGH = ["**/auth/**", "**/*auth*", "**/migrations/**", "**/*.sql", "**/payment*/**", "**/*payment*", "**/*billing*", ".github/**", "package.json", "**/package.json", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "pyproject.toml", "**/requirements*.txt", "**/poetry.lock", "**/uv.lock", "**/go.mod", "**/Cargo.toml", "**/pytest.ini", "**/setup.cfg", "**/tox.ini", "**/conftest.py", "**/.eslintrc*", "**/eslint.config.*", "**/tsconfig*.json", "**/vitest.config.*", "**/jest.config.*", "**/playwright.config.*", "delegate.toml"]`
  - `pyproject.toml`: `[project] name="delegate-cli" requires-python=">=3.11" dependencies=[]`, `[project.scripts] delegate="delegate.cli:main"`, `[project.optional-dependencies] dev=["pytest>=8"]`.
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** commit `feat: package skeleton and config`.

### Task 2: Git helpers and task state

**Files:**
- Create: `delegate/git.py`, `delegate/state.py`, `tests/conftest.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `config.Config`, `config.match_glob`
- Produces:
  - `git.DelegateError(Exception)`: the base error type; cli prints `delegate: <msg>`, exit 1.
  - `git.main_root(cwd: Path) -> Path` (via `git rev-parse --git-common-dir`; works from inside a worktree)
  - `git.head(tree: Path) -> str`
  - `git.status_files(tree: Path) -> list[str]`: modified + untracked, posix-relative, excluding `.delegate/`
  - `git.file_sha(tree: Path, rel: str) -> str` (sha256 of the bytes, or `"<deleted>"`)
  - `state.Task` dataclass: `id, title, created, base_commit, scope: list[str], dirty: dict[str, str], worktree: str | None, contributors: list[str], gates: dict, reviews: list[dict], triage: dict, approval: dict | None, outcome: str | None, events: list[dict]`
  - `state.create_task(repo: Path, task_id: str, title: str, scope: list[str], cfg: Config, worktree: bool = False) -> Task`
  - `state.load_task(repo: Path, task_id: str) -> Task` / `state.save_task(repo: Path, task: Task) -> None` (atomic: write tmp, `os.replace`)
  - `state.task_dir(repo: Path, task_id: str) -> Path` = `main_root/.delegate/tasks/<id>`
  - `state.task_lock(repo: Path, task_id: str)` context manager: `O_CREAT|O_EXCL` lock file; held → `DelegateError("task <id> is busy")`; a lock older than 2 h counts as stale and is replaced
  - `state.task_tree(repo: Path, task: Task) -> Path` (the worktree path or the main root)
  - `state.changed_files(task: Task, tree: Path) -> list[str]`: vs `base_commit` + untracked, minus dirty-at-start files whose sha is unchanged
  - `state.touched_dirty(task: Task, tree: Path) -> list[str]`
  - `state.out_of_scope(task: Task, files: list[str]) -> list[str]`
  - `state.hash_diff(task: Task, tree: Path) -> tuple[str, str]` → `(diff_text, sha256_hex)`, over `changed_files`, using `git add -N` for untracked, `git -c core.autocrlf=false -c core.safecrlf=false diff --no-color --no-ext-diff <base> -- <files>`
- `conftest.py` fixtures: `repo(tmp_path)` creates `tmp_path/"repo with space"`, `git init -b main`, sets local user.name/email, commits `README.md`, returns the Path; `delegate_home(tmp_path, monkeypatch)` sets `DELEGATE_HOME`.

- [ ] **Step 1: Write failing tests** in `tests/test_state.py`:
  - `test_create_records_base_and_excludes_state_dir`: `create_task(repo,"t1","x",["src/**"],cfg)` → `base_commit == git.head(repo)`; `.git/info/exclude` contains `.delegate/`; `task_dir(...)/"state.json"` exists; `.gitignore` not created.
  - `test_bad_id_rejected`: ids `"../x"`, `""`, `"a b"` → `DelegateError`; the allowed pattern is `[A-Za-z0-9._-]{1,64}`.
  - `test_duplicate_task_rejected`.
  - `test_dirty_files_excluded_until_touched`: user edits `README.md` before `create_task` → `changed_files` excludes it; append to it after → `touched_dirty == ["README.md"]` and it appears in `changed_files`.
  - `test_untracked_new_file_in_diff_and_hash_changes`: create `src/a.py` → in `changed_files`; hash changes after editing it.
  - `test_hash_stable_with_autocrlf`: set `core.autocrlf=true`, write a CRLF file, call `hash_diff` twice → equal.
  - `test_unicode_and_space_paths`: file `"مجلد/ملف.py"` appears in `changed_files` exactly as written.
  - `test_out_of_scope`: scope `["src/**"]`, files `["src/a.py","x.py"]` → `["x.py"]`.
  - `test_not_a_repo_and_no_commits`: plain dir → `DelegateError` message contains `not a git repository`; `git init` without commits → message contains `no commits`.
  - `test_lock_busy`: holding `task_lock` while acquiring again → `DelegateError` matching `is busy`.
  - `test_worktree_task_state_in_main_repo`: `create_task(..., worktree=True)` → worktree exists at `<repo>-wt-t1` on branch `delegate/t1`; `load_task(<worktree path>, "t1")` finds the same task.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Use `subprocess.run([...], cwd=..., capture_output=True, encoding="utf-8")` and set `core.quotepath=false`. `worktree_setup`, when configured, runs once in the new worktree via the runner from Task 3 (stub the call until Task 3 lands; Task 3's Step 3 wires it).
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** commit `feat: git helpers and task state`.

### Task 3: Runner with process-tree timeout, plus the fake CLIs

**Files:**
- Create: `delegate/runner.py`, `tests/fakes/fake_cli.py`
- Modify: `tests/conftest.py` (add the `fakes` fixture), `delegate/state.py` (wire `worktree_setup`)
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces:
  - `runner.RunResult` dataclass: `exit_code: int | None, timed_out: bool, unavailable: bool, duration: float, log_path: Path`
  - `runner.run(cmd: list[str], cwd: Path, log_path: Path, timeout: int, stdin_path: Path | None = None, stdout_path: Path | None = None) -> RunResult`: stderr (and stdout when `stdout_path` is None) go to the log; on timeout kills the tree (`taskkill /T /F /PID` on Windows; `start_new_session=True` + `os.killpg` on POSIX)
  - `runner.run_shell(command: str, cwd: Path, log_path: Path, timeout: int) -> RunResult` (for gates and worktree setup)
  - `runner.run_parallel(jobs: list[Callable[[], RunResult]]) -> list[RunResult]` (ThreadPoolExecutor, order preserved)
  - `runner.log_tail(path: Path, n: int = 20) -> str`
  - `runner.is_unavailable(text: str) -> bool`: true for quota / rate limit / usage limit / 429 / "not logged in" / "login required" / "Error authenticating" (case-insensitive)
  - `runner.resolve(name: str) -> str` (`shutil.which`, else `DelegateError("<name> not found on PATH")`)
- `fakes` fixture: writes `codex` and `agy` launchers into a tmp bin dir prepended to PATH (`.cmd` files on Windows, executable sh on POSIX), each running `python tests/fakes/fake_cli.py <codex|agy> <args>`. Behavior comes from env `FAKE_CODEX` / `FAKE_AGY`: comma-separated modes consumed one per call (counter file in the bin dir), last mode repeats. Modes: `none` (output `NO FINDINGS`), `findings` (two valid lines: one 🔴 on `src/a.py:1`, one 🟢), `prose`, `empty`, `hang` (spawns a child that sleeps 300s, then sleeps), `quota` (prints `You've hit your usage limit` to stderr, exit 1), `write:<rel>:<text>` (worker writes a file in `-C` dir). `codex login status` → `Logged in using ChatGPT`; `agy models` → a list including `gemini-3.8-flash-high`. Codex writes its output to the `-o` path; agy prints to stdout.

- [ ] **Step 1: Write failing tests:**
  - `test_run_ok_logs`: `python -c "import sys;print('hi',file=sys.stderr)"` → `exit_code == 0`, `"hi" in log_tail(log)`.
  - `test_timeout_kills_tree`: fake `hang`, timeout 2 → `timed_out`; the child PID the fake writes to a file is no longer alive within 5s.
  - `test_unavailable_detected`: fake `quota` → `unavailable is True`.
  - `test_parallel_runs_concurrently`: two 2-second sleeps → total under 3.5s, results in order.
  - `test_stdin_and_stdout_paths`: stdin file is piped; stdout lands in `stdout_path`.
  - `test_resolve_missing`: `resolve("definitely-missing-xyz")` raises `DelegateError`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement, and wire `worktree_setup` in `state.create_task` through `run_shell` (non-zero exit → `DelegateError` with the log tail).
- [ ] **Step 4:** pass (on Windows too: run locally).
- [ ] **Step 5:** commit `feat: runner with process-tree timeouts and fake CLIs`.

### Task 4: `task new`, `scope add`, `work`, `status` (basic)

**Files:**
- Modify: `delegate/cli.py`
- Create: `delegate/work.py`
- Test: `tests/test_cli_work.py`

**Interfaces:**
- Consumes: state, runner, config
- Produces:
  - CLI: `delegate task new <id> --title T --files G [G...] [--worktree]`, `delegate scope <id> add G [G...]`, `delegate work <id> --brief FILE`, `delegate status [<id>]` (until Task 8, `status` prints the changed files, `out_of_scope` and `touched_dirty` only)
  - `work.run_worker(repo: Path, task: Task, cfg: Config, brief: Path) -> RunResult`: refuses (exit 1) when `touched_dirty` is non-empty, with message `commit or stash your edits to <file> first`; copies the brief into the task dir; runs the Codex worker with `timeouts.worker`; on any exit appends `"codex"` to `task.contributors` (once) if the diff_hash changed; records the event `{"type":"work","diff_hash":…,"exit":…,"timed_out":…,"unavailable":…,"duration":…}`; the CLI prints `result.md` capped at 15 lines plus `git diff --stat`; on a non-zero exit, a timeout or an unavailable run it also prints `log_tail` (20 lines) and exits 1.
  - Every command that reads or writes task state runs inside `task_lock`.
- [ ] **Step 1: Write failing tests** (with fakes):
  - `test_work_writes_and_records_codex`: `FAKE_CODEX=write:src/a.py:x=1` → exit 0; `src/a.py` exists; task contributors == `["codex"]`; one `work` event with a diff_hash.
  - `test_work_refuses_touched_dirty`: README dirty at start and then modified → exit 1, stderr contains `commit or stash`.
  - `test_work_timeout_reported`: `FAKE_CODEX=hang` and `[timeouts] worker=2` → exit 1, stderr contains `timed out`, the event has `timed_out` true.
  - `test_scope_add`: after `scope t1 add x.py`, `out_of_scope` for `x.py` is empty.
  - `test_unknown_task`: `work nope --brief b.md` → exit 1, `no task named nope`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** pass. **Step 5:** commit `feat: task new, scope, work`.

### Task 5: Review building blocks (pure functions)

**Files:**
- Create: `delegate/review.py`
- Test: `tests/test_review_parse.py`

**Interfaces:**
- Produces:
  - `review.Finding` dataclass: `id: str, reviewer: str, round: int, severity: str` (`"blocker"|"major"|"nit"`), `file: str, line: int, problem: str, why: str`
  - `review.Validation` dataclass: `kind: str` (`"findings"|"none"|"could_not_read"|"invalid"`), `findings: list[tuple[str, str, int, str, str]]` (severity, file, line, problem, why)
  - `review.validate_output(text: str) -> Validation`: strip whitespace and code fences. Exactly `NO FINDINGS` → none; `COULD NOT READ …` → could_not_read; otherwise **every** non-blank line must parse or the result is invalid; empty → invalid.
  - `review.SecretHit` dataclass: `file: str, line: int, kind: str, masked: str`
  - `review.scan_secrets(diff: str, allow: list[str]) -> list[SecretHit]`: only added lines (`+`, not `+++`); any `.env`/`.env.*` file in the diff is itself a hit (kind `env-file`); files matching `allow` are skipped; `masked` = first 4 chars + `…`. When `gitleaks` is on PATH, also run `gitleaks detect --no-git --pipe`-style over the added text and merge its hits (if the gitleaks run fails, warn and continue).
  - `review.render_brief(goal: str, criteria: str, diff_path: Path, tree: Path, changed_lines: dict[str, list[int]], minutes: int) -> str`: contains the goal, the criteria, both absolute forward-slash paths, the no-shell line, `Spend at most ~{minutes} minutes; do not build test harnesses.`, the "Recently changed" file:line ranges, the exact output-format rules, `If you could not read the change, output exactly: COULD NOT READ <path>`, `If you find nothing, output exactly: NO FINDINGS`, and nothing about earlier rounds.
  - `review.changed_lines(diff: str) -> dict[str, list[int]]` (new-side line numbers of added lines)
- [ ] **Step 1: Write failing tests:**
  - `test_validate_variants`: `"NO FINDINGS"` → none; `"```\nNO FINDINGS\n```"` → none; `"🔴 src/a.py:12 — bad — breaks x"` → findings `[("blocker","src/a.py",12,"bad","breaks x")]`; the same line with ` - ` separators parses; `"Looks good!"` → invalid; `""` → invalid; one valid line plus one prose line → invalid; `"COULD NOT READ C:/x"` → could_not_read.
  - `test_scan_secrets_hits` (parametrized added lines, each must hit): `-----BEGIN RSA PRIVATE KEY-----`, `AKIAABCDEFGHIJKLMNOP`, `sk-` + 40 alnum, `ghp_` + 36 alnum, `github_pat_` + 40 chars, `AIza` + 35 chars, a three-part base64url JWT starting `eyJ`, `api_key = "abcd1234abcd1234abcd"`, `PASSWORD: 'Sup3rS3cretValue!!'`.
  - `test_scan_secrets_misses`: removed lines, `password = os.environ["X"]`, `token = ""` → no hits.
  - `test_env_file_is_hit` and `test_allowlist_skips_fixture_path`.
  - `test_brief_has_no_history_and_rules`: the rendered brief contains `NO FINDINGS`, the no-shell line, the goal and the criteria, and does not contain `round` or `previous`.
  - `test_changed_lines`: a two-hunk diff → correct new-side numbers.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** pass. **Step 5:** commit `feat: review parsing, secret scan, brief`.

### Task 6: `review` and `triage` commands

**Files:**
- Modify: `delegate/review.py`, `delegate/cli.py`
- Test: `tests/test_review_run.py`

**Interfaces:**
- Consumes: Task 5 functions, `runner.run_parallel`, `state.hash_diff`, `state.touched_dirty`
- Produces:
  - `review.REVIEW_EXCLUDE = ["**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "**/poetry.lock", "**/uv.lock", "**/*.min.js", "**/*.min.css", "**/*.map"]`. These files stay in the hash and in risk; they are left out of `diff.patch`, which gets one line per omitted file: `# omitted from review: <file>`.
  - `review.run_review(repo: Path, task: Task, cfg: Config, goal: str, criteria: str) -> dict` (the round record), with the refusal order:
    1. no changes → exit 2 `nothing to review`
    2. `touched_dirty` → exit 1
    3. 3 rounds already exist → exit 2 `review cap reached (3): ask the human — delegate approve <id> or delegate abandon <id>`
    4. secret hits → exit 2, listing `file:line kind masked`
  - The run then writes `diff.patch` and `review_brief.md` in the task dir, deletes `review_codex.md` and `review_gemini.md` by exact name, and runs both reviewers in parallel with their timeouts. An `invalid` or `could_not_read` result, or a non-unavailable failure, is retried once (just that reviewer). Reviewer status:
    - `ran`: valid findings/none
    - `unavailable`: `is_unavailable` on the log
    - `not_run`: anything else after the retry
  - Round record: `{"round": n, "diff_hash": h, "reviewers": {"codex": {"status": s, "findings": [Finding as dict]}, "gemini": {...}}, "at": iso}`. Finding ids are `<reviewer>-<round>-<k>`, k from 1 per reviewer.
  - The CLI prints all findings sorted by (file, line), with ids, plus each reviewer's status.
  - CLI: `delegate review <id> --goal TEXT [--criteria-file FILE]`; `delegate triage <id> <finding-id> fix|contest "<reason>" [--same-as <old-id>]` (with `--same-as`, an empty reason copies the old reason; an unknown finding id → exit 1). Triage entries are `{"decision","reason","same_as","diff_hash","at"}` keyed by finding id.
- [ ] **Step 1: Write failing tests** (with fakes):
  - `test_review_both_ran_findings_sorted`: `FAKE_CODEX=findings FAKE_AGY=none` → round 1; codex `ran` with 2 findings `codex-1-1`, `codex-1-2`; gemini `ran` with 0.
  - `test_invalid_then_valid_retry`: `FAKE_AGY=prose,none` → gemini `ran`.
  - `test_invalid_twice_not_run`: `FAKE_AGY=prose` → gemini `not_run`.
  - `test_quota_unavailable`: `FAKE_AGY=quota` → `unavailable`, no retry (the counter shows 1 call).
  - `test_secret_blocks_send`: a changed file contains `ghp_` + 36 chars → exit 2; neither fake was called (counter files absent).
  - `test_round_cap`: three rounds → the fourth exits 2 with `review cap reached`.
  - `test_lockfile_omitted_from_patch_but_in_hash`: changing only `package-lock.json` → `diff.patch` has `# omitted from review: package-lock.json`; the hash differs from the hash before the change.
  - `test_brief_points_to_patch_in_taskdir`: `review_brief.md` contains the absolute forward-slash `diff.patch` path inside `.delegate/tasks/t1/`.
  - `test_triage_records_and_same_as`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** pass. **Step 5:** commit `feat: review and triage commands`.

### Task 7: Gates, risk, and `verify`

**Files:**
- Create: `delegate/gates.py`
- Modify: `delegate/cli.py`
- Test: `tests/test_gates.py`

**Interfaces:**
- Produces:
  - `gates.run_gates(repo: Path, task: Task, cfg: Config) -> dict[str, dict]`: each gate runs sequentially through `runner.run_shell` in `state.task_tree(...)` with `timeouts.gate`, recording `{"status": "pass"|"fail"|"timeout", "exit": int|None, "diff_hash": h, "log": str, "at": iso}`. The hash is computed **before** the gates run, and if a gate itself changes the code (hash after ≠ before), every result is stored as `fail` with reason `gate modified files`.
  - `gates.compute_risk(files: list[str], cfg: Config) -> str`:
    - `high` if any file matches `risk_high`
    - else `low` if all files match `risk_low` (and there's at least one file)
    - else `medium`
  - CLI `delegate verify <id>` prints `PASS|FAIL|TIMEOUT name` lines, plus the log tail for failures. Exit 0 when all pass, else 2.
- [ ] **Step 1: Write failing tests:**
  - `test_gates_sequential_all_run`: gates `a` (exit 1) and `b` (exit 0) → both recorded; `a` fail, `b` pass; exit 2.
  - `test_gate_timeout`: `python -c "import time;time.sleep(30)"` with gate timeout 2 → `timeout`.
  - `test_gate_modifying_files_fails`: a gate that appends to a tracked file → all `fail`, reason recorded.
  - `test_risk_levels`: `["README.md"]` → low; `["src/a.py"]` → medium; `["src/auth/login.py"]` → high; `["delegate.toml","README.md"]` → high; `["package-lock.json"]` → high; `[]` → medium.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** pass. **Step 5:** commit `feat: gates, risk, verify`.

### Task 8: Evaluation, `status`, `approve`, `done`, `abandon`

**Files:**
- Modify: `delegate/gates.py`, `delegate/cli.py`
- Test: `tests/test_done.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `gates.Evaluation` dataclass: `risk: str, diff_hash: str, files: list[str], blockers: list[str], next_cmd: str, phase: str`
  - `gates.evaluate(repo: Path, task: Task, cfg: Config) -> Evaluation`, with these rules, all checked against the **current** hash:
    - **B1** no changed files → `no changes`
    - **B2** `out_of_scope` → `out of scope: <f>` (one blocker per file)
    - **B3** `touched_dirty` → `your uncommitted edits were changed: <f>`
    - **B4** no gates configured and not `allow_no_gates` → `no gates configured (add [gates] to delegate.toml)`
    - **B5** each configured gate without a `pass` result at the current hash → `gate <name>: <missing|stale|fail|timeout>`
    - **Review requirement** (medium or high), applied to the latest round at the current hash, or `review missing/stale`:
      - required reviewers: high → `codex` and `gemini`; medium → `gemini` if `"codex" in contributors`, else either one
      - **B6** a required reviewer without status `ran` → `reviewer <r>: <status>`
      - **B7** any 🔴/🟡 finding of that round not triaged `contest` → `finding <id> open` (a `fix` decision is still open until a new round passes)
    - **B8** high risk, or the B6/B7 override: approval missing, or approval `diff_hash` ≠ current → `human approval required: run 'delegate approve <id>' in your own terminal`. A valid approval satisfies B6/B7 for any risk (recorded as an override).
    - `phase` is the first applicable of: `no-changes`, `needs-verify`, `needs-review`, `needs-triage`, `needs-approval`, `ready`. `next_cmd` is the matching exact command.
  - `gates.approve(repo, task, cfg, isatty: Callable[[], bool] = sys.stdin.isatty, ask: Callable[[str], str] = input) -> None`:
    - refuses with `DelegateError("approve must be run by a human in an interactive terminal")` when `not isatty()`
    - otherwise prints the risk, files, gate results, findings and their triage, then requires typing the task id
    - records `{"diff_hash", "at", "override": bool}`
  - CLI:
    - `delegate status [<id>]`: all open tasks are `id  phase  risk`; one task is the full Evaluation
    - `delegate approve <id>`
    - `delegate done <id>`: exit 2 with the blockers, or marks `outcome="done"`, records metrics (Task 9 hook: `metrics.record` is called if importable, otherwise skipped until Task 9), and prints merge instructions for worktree tasks: `git merge delegate/<id>` then `git worktree remove <path>`
    - `delegate abandon <id>`
- [ ] **Step 1: Write failing tests** (a helper builds task states directly, so each rule is tested in isolation):
  - `test_low_risk_needs_only_gates` (README change + passing gate → `ready`, `done` exit 0).
  - `test_stale_gate_after_edit` (pass, then edit → `gate test: stale`).
  - `test_missing_gate_never_passes` (gate configured but never run → `gate test: missing`).
  - `test_no_gates_blocks_unless_allowed`.
  - `test_medium_codex_written_requires_gemini` (only codex ran clean → blocker `reviewer gemini: missing`).
  - `test_medium_claude_written_either_reviewer`.
  - `test_open_major_blocks_until_contested` (🟡 untriaged → blocked; `fix` → blocked; `contest` → ready).
  - `test_high_requires_both_and_approval`.
  - `test_approval_goes_stale_on_edit`.
  - `test_approve_refuses_without_tty` (`isatty=lambda: False` → `DelegateError`).
  - `test_approve_overrides_unavailable_reviewer` (gemini unavailable + approval at the current hash → ready; override recorded).
  - `test_out_of_scope_blocks`.
  - `test_status_next_cmd` (each phase prints the right command).
  - `test_done_marks_outcome_and_prints_merge_for_worktree`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** pass. **Step 5:** commit `feat: evaluation, status, approve, done`.

### Task 9: Metrics, `report`, `doctor`, `init`

**Files:**
- Create: `delegate/metrics.py`, `delegate/setup.py`
- Modify: `delegate/cli.py`
- Test: `tests/test_metrics.py`, `tests/test_setup.py`

**Interfaces:**
- Produces:
  - `metrics.home() -> Path` (`DELEGATE_HOME` or `~/.delegate`)
  - `metrics.record(task: Task, cfg: Config, outcome: str, evaluation: Evaluation | None) -> dict`: appends one JSON line with keys `project, task, risk, outcome, started, ended, wall_seconds, work_runs, worker_timeouts, review_rounds, findings` (`{reviewer: {blocker, major, nit}}`), `triage` (`{fix, contest}`), `gates` (`{pass, fail, timeout}`), `reviewer_status` (`{reviewer: {ran, not_run, unavailable}}`), `override`. It never stores file contents, finding text, or briefs.
  - `metrics.report(path: Path) -> str`: tasks by outcome, median wall time, mean rounds, findings per reviewer by severity, contest rate, not_run/unavailable per reviewer. Prints `no tasks recorded yet` when the file is empty or missing.
  - `setup.doctor() -> list[tuple[str, bool, str]]`: `codex login status` contains `ChatGPT`; `agy models` lists the configured model; `git --version`; Python ≥ 3.11. The CLI prints `PASS|FAIL name detail` and exits 2 if any check fails.
  - `setup.init(repo: Path) -> Path`:
    - writes `delegate.toml`, refusing to overwrite (exit 1)
    - suggests gates from what exists:
      - `package.json` scripts `lint` / `typecheck` / `test` → `npm run <script>`
      - `pyproject.toml` mentioning `ruff` → `ruff check .`
      - `tests/` or a pytest config → `python -m pytest -q`
      - nothing found → `[gates]` left empty, with a comment
    - also adds `.delegate/` to `.git/info/exclude`
- [ ] **Step 1: Write failing tests:**
  - `test_record_has_no_content`: finding text `SECRET_PROBLEM_TEXT` is absent from the jsonl line, and the counts are correct.
  - `test_report_empty_and_aggregates`.
  - `test_doctor_with_fakes_all_pass`, and `test_doctor_missing_model_fails` (fake agy lists another model).
  - `test_init_detects_npm_scripts`, `test_init_detects_pytest`, `test_init_refuses_overwrite`.
- [ ] **Step 2:** FAIL. **Step 3:** implement, and turn the Task 8 metrics hook into a direct call. **Step 4:** pass. **Step 5:** commit `feat: metrics, report, doctor, init`.

### Task 10: Ship — skill, docs, CI, real smoke test

**Files:**
- Modify: `SKILL.md`, `README.md`
- Create: `docs/manual.md`, `.github/workflows/ci.yml`, `scripts/smoke_real.py`
- Test: CI run + `scripts/smoke_real.py` on the owner's machine

- [ ] **Step 1:** Move the v1 recipe sections (Setup per worker, Recipes, templates, triage/loop) from `SKILL.md` into `docs/manual.md`, unchanged.
- [ ] **Step 2:** Rewrite `SKILL.md`:
  - keep the frontmatter, Roles, and "Don't delegate"
  - add the sequence `delegate task new → work → verify → review → triage → (fix → verify → review) → done`
  - **Rules:**
    - read `delegate status <id>` after any interruption
    - never edit `.delegate/`, `delegate.toml`, gate or test config to get past a blocker; tell the user instead
    - `approve` is the human's, so ask them to run it
    - if `delegate` isn't on PATH, use `docs/manual.md`
  - **Install line:** `pip install --user git+https://github.com/taherfarg/claude-delegate-skill`
- [ ] **Step 3:** Rewrite `README.md`:
  - **Lead:** what it is and why
  - **Diagram:** a mermaid flow (Claude → delegate → Codex/agy → gates → done)
  - **Explanations:**
    - the risk table
    - the command table
    - install (skill clone + pip)
    - the `delegate.toml` example
  - **Metrics:** a "Metrics" section to be filled with real `delegate report` output after first use
  - **Credits:** EGM
  - **License:** MIT
- [ ] **Step 4:** Write `.github/workflows/ci.yml`:
  - matrix `os: [windows-latest, ubuntu-latest, macos-latest]` × `python: ["3.11","3.12","3.13"]`
  - steps: `pip install -e .[dev]`, then `python -m pytest -q`
- [ ] **Step 5:** Write `scripts/smoke_real.py`. It makes a temp git repo and runs `delegate task new`, `work` (real Codex, a 1-function brief), `verify` (gate `python -c "import a"`), `review` (real Codex + agy), then `status`. It prints each step's exit code and timing. It is never run in CI.
- [ ] **Step 6:** Verify:
  - `python -m pytest -q` passes locally
  - push the branch, and the CI matrix is green (`gh run watch`)
  - `python scripts/smoke_real.py` completes with both reviewers `ran`
- [ ] **Step 7:** commit `docs: v2 skill, README, CI, real smoke test`.
