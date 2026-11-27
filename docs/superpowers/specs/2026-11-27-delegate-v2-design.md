# delegate v2 — the enforcement engine

Status: draft for owner approval · 2026-11-27

## Intent

`delegate` v1 is a Claude Code skill: Claude (the "elephant") orchestrates and hands coding to Codex CLI (ChatGPT subscription) and fresh-context reviews to Codex + Gemini via Antigravity `agy` (the "goldfish"). In two real test runs every failure was **mechanical** — Claude following prose and getting a step wrong (shell variables lost between calls, a cleanup glob deleting the brief, no timeouts, empty Gemini output, a stale browser cache treated as current). Prose can't enforce; code can.

v2 adds a small command-line engine, `delegate`, that **Claude calls**. Claude stays the brain (plans, writes briefs, triages, decides). The engine owns the mechanics and the gates, so steps can't be skipped or faked.

Two goals, equal weight: (1) the owner's daily driver across projects (Windows 11 primary), (2) a flagship open-source portfolio project.

## Goals

- Mechanical steps are commands, not prose: run worker, run reviewers, run checks, all with timeouts, logs, and validated output.
- **Evidence-based gates:** a task can only be marked done when every required check ran **on the current code**. A missing, stale, or failed check never counts as a pass.
- **Risk-based rules:** low-risk changes need checks only; medium needs checks + a clean independent review; high-risk (auth, DB, payments, CI, dependencies, gate config) needs checks + both reviewers clean + the human's approval typed in a real terminal.
- **Resume:** all task state lives on disk; after a usage limit or a new session, `delegate status` says exactly where the task stands and what's next.
- **Protect the user:** reviewer diffs are scoped to the task's files, pre-existing uncommitted user changes are never sent, and a secret scan blocks sending anything that looks like a credential.
- **Measured:** every finished task appends a metrics line; `delegate report` turns that into real numbers (time, rounds, findings per reviewer, timeouts) — the "factory evaluation", from real use not synthetic benchmarks.
- Runtime is **Python ≥ 3.11 standard library only** (no dependencies). Cross-platform; tested on Windows, Linux, macOS in CI.

## Non-goals (v2)

- **Event triggers** (GitHub issues, failed CI, Sentry): subscription logins need a person at the keyboard; unattended runs would need API keys — a different product.
- **Own memory store (SQLite):** Claude Code already has memory and per-project `CLAUDE.md`.
- **Claude adapter / engine driving Claude:** Claude is the caller, never the callee. Inverting that breaks the elephant model.
- Dashboard, auto-merge, auto-deploy, bundling linters (projects bring their own checks).

## Anti-goals (failure even if it "works")

- The engine reports success for a check that didn't run or ran on older code.
- A worker (or Claude) can weaken the rules by editing the config or test setup without the human noticing.
- Claude can approve a high-risk task on the human's behalf.
- User secrets or unrelated uncommitted edits leave the machine.
- Daily use gets slower or more ceremonious than v1 for small tasks.

## Approaches considered

| | Approach | Verdict |
|---|---|---|
| A | Full "software factory" (Python orchestrator driving all three models, SQLite memory, event triggers, dashboard) — the ChatGPT proposal | Rejected: inverts the elephant model, its triggers can't use subscription auth, mostly speculative |
| **B** | **Small stdlib engine Claude calls; skill stays the brain** | **Chosen**: fixes every observed failure, real enforcement, credible portfolio code |
| C | Tighten SKILL.md prose only | Rejected: enforces nothing; thin as a portfolio piece |

The owner delegated the choice of approach to Claude ("you pick").

## Design

### Shape

```
delegate/            # package (stdlib only)
  cli.py             # argparse entry: `delegate <command>`
  config.py          # delegate.toml loading + defaults
  state.py           # task state on disk, diff hashing, derived status
  runner.py          # run external CLIs: timeout, kill process tree, logs
  review.py          # diff freeze, secret scan, brief render, output validation, findings
  gates.py           # project checks + done/approve rules
  metrics.py         # metrics.jsonl append + report
tests/               # pytest, with fake `codex`/`agy` executables
SKILL.md             # short: when to delegate + use the CLI; links docs/manual.md
docs/manual.md       # v1 manual recipes (fallback when the CLI isn't installed)
pyproject.toml       # console script `delegate`; pytest as dev extra only
```

Install: `pip install --user git+https://github.com/taherfarg/claude-delegate-skill` (or `pip install -e .` from a clone). The skill itself is still installed by cloning into `~/.claude/skills/delegate`.

### Task state

- Lives in `<repo>/.delegate/tasks/<id>/` (`state.json`, briefs, logs, review outputs). `.delegate/` is added to `.git/info/exclude` — never to the user's tracked `.gitignore`.
- At `task new`: record `base_commit` (immutable baseline) and a snapshot (path + content hash) of files that were **already dirty**. Those are the user's edits: excluded from every diff sent out unless the task also changes them (then flagged).
- **Scope:** `task new --files <globs>` declares what the task may touch. Changed files outside scope are flagged OUT OF SCOPE and block `done` until Claude adds them to scope (`delegate scope add`) or reverts them.
- **diff_hash** = sha256 of the scoped diff (base → working tree, untracked task files included via `git add -N`). Every gate result and review round records the diff_hash it ran on. If the code changes, they're **stale** automatically.
- **Status is derived from evidence, not a stored phase.** `delegate status <id>` computes: changes present? gates fresh & passing? review fresh & clean? findings all triaged? approval needed/present? → prints the state and the **next command** to run. That's resume.

### Commands

| Command | Does |
|---|---|
| `delegate doctor` | Checks `codex login status` says ChatGPT, `agy models` lists the configured Gemini model, git present, Python version. PASS/FAIL per line. |
| `delegate init` | Writes `delegate.toml` with gates suggested from the detected stack (package.json → lint/typecheck/test scripts that exist; pyproject → ruff/pytest if present), adds `.delegate/` to `.git/info/exclude`. |
| `delegate task new <id> --title … --files … [--worktree]` | Creates the task (baseline, dirty snapshot, scope, risk computed later from changed files). `--worktree` makes `<repo>-wt-<id>` on branch `delegate/<id>`; later commands run there. |
| `delegate work <id> --brief <file>` | Runs Codex worker (`codex exec -s workspace-write`) with timeout; saves result + log; prints result (≤15 lines) and `diff --stat`. |
| `delegate verify <id>` | Runs every configured gate in the task's tree; records pass/fail/timeout + diff_hash; prints failures with log tail. |
| `delegate review <id> [--goal …] [--criteria-file …]` | Freezes scoped diff → secret scan (blocks on hit) → renders goldfish brief (goal, criteria, paths, recently-changed lines, no chat history) → runs Codex + Gemini in parallel with timeouts → validates format, retries invalid once → findings with ids `<reviewer>-<round>-<n>`, sorted by file:line. Refuses a 4th round (cap 3). |
| `delegate triage <id> <finding-id> fix\|contest "<reason>"` | Records Claude's decision. A contest stays settled until the code at that file changes. |
| `delegate status [<id>]` | All tasks, or one task's derived state + next command. |
| `delegate approve <id>` | **Human only**: shows the summary and requires typed confirmation; refuses when stdin is not an interactive terminal, so Claude's Bash tool can't approve. |
| `delegate done <id>` | The gate. Refuses with reasons unless all rules for the task's risk level hold on the current diff_hash. Then appends metrics and prints merge instructions for worktree tasks (no auto-merge). |
| `delegate abandon <id>` | Ends a task, records metrics as abandoned. |
| `delegate report` | Summarizes `~/.delegate/metrics.jsonl` across projects. |

### Rules enforced by `done`

| Risk | Decided by | Required on current diff_hash |
|---|---|---|
| low | every changed file matches `risk.low` globs (default: docs, `*.md`, `*.css`, images) | all gates pass |
| medium | default | all gates pass + a clean review round including at least one reviewer that didn't write the code (Codex-written → Gemini required) + all 🔴/🟡 triaged |
| high | any changed file matches `risk.high` globs (default: auth, migrations/`*.sql`, payment/billing, `.github/**`, dependency manifests, test/lint config, `delegate.toml`) | all gates pass + **both** reviewers ran clean + all 🔴/🟡 triaged + human `approve` |

Also always: no OUT OF SCOPE files; no gate configured → refuse unless `delegate.toml` explicitly says `allow_no_gates = true`. A round is "clean" when no new untriaged 🔴/🟡 exists and each required reviewer has status `ran`. Changing `delegate.toml` or gate-related config inside a task forces **high** risk (workers can't quietly weaken checks).

### External CLI runs (`runner.py`)

- Prompts always via brief files; paths passed as absolute, forward-slash form.
- Default timeouts: worker 15 min, Codex review 5 min, agy `--print-timeout 8m`, each gate 10 min (all configurable).
- On timeout, kill the **whole process tree** (`taskkill /T /F` on Windows, process group on POSIX) — v1's `timeout` left children alive.
- Logs to the task dir; on failure print only the last 20 lines.
- Fixed invocations (no bypass flags, ever): Codex worker `-s workspace-write`, Codex review `-s read-only --ephemeral`, agy `--mode plan --model <gemini model> --add-dir <taskdir>`; agy briefs always include the no-shell line.
- Detect quota/rate-limit/login errors in logs → status `unavailable` (distinct from `failed`), so Claude knows to fall back.

### Review output validation

Valid output is exactly `NO FINDINGS`, `COULD NOT READ <path>`, or one-or-more lines matching `<🔴|🟡|🟢> <file>:<line> — <problem> — <why>` (tolerating `-`/`–` for `—`). Anything else (empty, prose, partial) = invalid → one retry → reviewer status `not_run`. Old outputs are deleted by exact name before each run.

### Secret scan

Before any diff or brief leaves the machine: regexes for private-key headers, AWS keys, `sk-…`, `ghp_…`/`github_pat_…`, `AIza…`, JWT-shaped strings, generic `key|token|secret|password = "<16+ chars>"`, and any `.env*` file in scope. Uses `gitleaks` too when installed. Any hit blocks the send and prints file:line with the value masked.

### Metrics (no code, no secrets)

One JSON line per finished/abandoned task in `~/.delegate/metrics.jsonl`: project name, task id, risk, outcome, wall time per phase, review rounds, findings by severity × reviewer, fix/contest counts, gate pass/fail counts, reviewer `not_run`/timeout/unavailable counts. `delegate report` prints totals and per-reviewer stats.

### SKILL.md in v2

Shrinks to: roles, when (not) to delegate, the command sequence (`task new → work → verify → review → triage → fix → verify → review → done`), and "never edit `.delegate/` or `delegate.toml` to get past a gate — tell the user". If `delegate` isn't installed, follow `docs/manual.md` (today's recipes) — backward compatible.

### Testing the engine

- pytest, with fake `codex` and `agy` executables put first on PATH in tests; behavior chosen per test (valid findings, `NO FINDINGS`, prose, empty, hang past timeout, quota error, write files).
- Covered: staleness via diff_hash, scope + dirty-file protection, secret blocking, format validation + retry, process-tree kill on timeout, every `done` refusal per risk level, `approve` refusing without a TTY, metrics lines, `status` next-command for each state.
- GitHub Actions: windows/ubuntu/macos × Python 3.11–3.13, no subscriptions needed thanks to the fakes. One manual "real CLIs" smoke script for the owner's machine.

### Delivery in phases (each independently testable)

1. **Core:** state + diff_hash + scope/dirty protection, `task new`, `work`, `status`, runner with tree-kill.
2. **Review:** `review` (freeze, secret scan, parallel run, validation, retry, findings), `triage`, round cap.
3. **Gates:** `delegate.toml`, `verify`, risk rules, `approve`, `done`, `abandon`.
4. **Ship:** `doctor`, `init`, metrics + `report`, new SKILL.md + `docs/manual.md`, README (diagram, why, real `report` numbers), CI matrix.

## Builder-check answers (Gemini read the draft cold; Claude's calls unless noted)

1. **Task touches a file the user already had dirty** → `work`/`review` refuse and say "commit or stash your edits to <file> first". No diff surgery. *(Claude's call: simplest, and the only option that never leaks user edits.)*
2. **Re-reported contested findings** → no fuzzy matching. Each round's findings are new ids; Claude triages them, and can link a repeat with `triage <id> <new> contest --same-as <old>`. The goldfish brief never lists settled findings (that would leak reasoning). *(Claude's call.)*
3. **Authorship & unavailable reviewers** → the engine records who produced each diff_hash: changes made during `work` = `codex`, any other change = `claude`. The reviewer that didn't write it is required (Claude-written → either counts). If a required reviewer is `unavailable`/`not_run`, `done` refuses; the human can override with `delegate approve`, and the metrics record "single-reviewed by approval". *(Claude's call.)*
4. **Secret false positives** → path allowlist `[secrets] allow = ["tests/fixtures/**", …]` in `delegate.toml`. Editing it inside a task forces high risk like any other `delegate.toml` change. *(Claude's call.)*
5. **Worktrees** → state always in the main repo's `.delegate/` (found via `git rev-parse --git-common-dir`); `work`, `verify` and `review` run inside the worktree; optional `[worktree] setup = "npm ci"` runs once after creation. *(Claude's call.)*
6. **Scope changes** → `delegate scope <id> add <glob>…`. Risk is derived from the changed files at check time, so if scope growth pulls in a high-risk file, `done` simply starts requiring both reviewers + approval. *(Already implied by "status is derived".)*
7. **How reviewers get the diff** → `diff.patch` in the task dir, referenced by absolute path. The task dir is inside the repo, so Codex read-only and agy (`--add-dir`) can both read it — verified in v1 runs. *(Answered.)*
8. **Gate execution** → sequential, never fail-fast: every gate runs so the evidence is complete. *(Claude's call: avoids port/DB collisions; slower is fine.)*

## Owner decisions on record

- Daily use **and** portfolio; Claude picked approach B.
- Keep the `delegate` name and the existing repo (rename is cosmetic; GitHub redirects if wanted later).
- Develop in a clone at `Desktop/PROJECTS/claude-delegate-skill` on branch `v2-engine`; the installed skill in `~/.claude/skills/delegate` stays v1 until v2 ships.

## Left to the builder

Exact regexes, argparse layout and help text, output formatting, internal function names, default glob lists beyond those named above.
