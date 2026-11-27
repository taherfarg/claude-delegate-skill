# Delegate Skill (Elephant + Goldfish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One global Claude Code skill that makes Claude the orchestrator ("elephant") and uses ChatGPT (Codex CLI) and Gemini (Antigravity CLI) as workers and fresh-context reviewers ("goldfish"), all on existing subscriptions.

**Architecture:** A single `SKILL.md` holding exact command recipes. No script, no MCP server: both CLIs already run headless, write output to files, and use worktrees. Claude writes each task brief to a file, runs the CLI in the background, and reads only the short result plus the diff.

**Tech Stack:** Codex CLI 0.166 (`codex exec`, ChatGPT login), Antigravity CLI 1.1.13 (`agy -p`, Google login, model `gemini-3.8-flash-high`).

**Spec (approved design, in chat 2026-11-18):**
- **Goals:** save Claude limits, get a second opinion and finish work faster, all three at once.
- **Elephant (Claude):** plans the work, writes task briefs, reads summaries and diffs, sorts findings, merges, and makes the final calls.
- **Codex (ChatGPT):** the main coding worker, with workspace-write sandbox and no network.
- **Gemini (agy):** read-only research, codebase scans and reviews (`--mode plan`).
- **Goldfish rule** (from google/elephant-goldfish-model): reviewers get only the diff or doc plus the paths it touches, never the chat history and never Claude's reasoning for why it's right. Codex and Gemini review in parallel, and the model that wrote the code never reviews its own diff alone.
- **Findings:** 🔴 Blocker / 🟡 Major / 🟢 Nit, plus `file:line` and a reason. Claude marks each one FIX or CONTEST with a written reason; nothing is silently dropped.
- **Loop:** stop after 2 consecutive clean rounds (no open 🔴/🟡 and every reviewer ran). Hard cap of 3 rounds, then the user decides. A crashed reviewer counts as "not run", never as clean.
- **Don't delegate:** tiny edits, anything with secrets or credentials, final judgment calls.
- **Never:** `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`, yolo modes.

## Global Constraints
- **Skill location:** `C:\Users\U\.claude\skills\delegate\SKILL.md` (global, so it works in every project).
- **Working folder:** briefs and outputs go to `$TEMP/delegate/<project>/<task-id>/` and never into the user's repo.
- **Gemini CLI:** `gemini` must not be used. Personal OAuth now fails with "client is no longer supported" (verified 2026-11-18), so use `agy`.
- **Gemini model:** always pass `--model gemini-3.8-flash-high`, because agy also offers Claude models and the goldfish must be a different model.
- **Prompts:** always pass via a brief file (`Read <path> and do it.`), to avoid Windows command-line length and quoting limits.

## Review Focus
1. **CLI hangs or times out:** the background run must end. agy uses `--print-timeout 15m`, and Codex runs in the background; a reviewer that times out is reported as "not run".
2. **Not a git repo:** Codex refuses unless `--skip-git-repo-check` is passed. Use it only for throwaway folders.
3. **Two coders on the same files:** each parallel Codex task gets its own git worktree via `--worktree`.
4. **A reviewer returns prose instead of findings:** Claude treats the run as failed, retries once, then records it as "not run".
5. **The Codex sandbox blocks installs or the network:** the brief tells Codex to report the blocked command instead of working around it.

---

### Task 1: Write the skill

**Files:**
- Create: `C:\Users\U\.claude\skills\delegate\SKILL.md`

**Interfaces:**
- **Produces:** the skill `delegate`, which auto-triggers on multi-step build work or when the user asks for codex/gemini/second opinion/review.

- [ ] **Step 1: Write `SKILL.md`** with frontmatter (`name: delegate`, a trigger-rich `description`) and these sections, each using the values pinned above:
  1. Roles table (Claude / Codex / Gemini)
  2. When NOT to delegate
  3. Recipes (exact commands, shown below)
  4. Goldfish review brief template (asks for the findings format, with no chat context)
  5. Triage + loop rules
  6. Safety
- [ ] **Step 2: Pin these recipes in the skill:**

```bash
# Codex worker (writes code)
codex exec -C "<repo>" -s workspace-write -o "$D/result.md" - < "$D/brief.md"
# parallel coder: add --worktree
# Codex goldfish (read-only)
codex exec -C "<repo>" -s read-only --ephemeral -o "$D/review_codex.md" - < "$D/review_brief.md"
# Gemini goldfish / researcher (read-only)
cd "<repo>" && agy -p "Read $D/review_brief.md and do exactly what it says." --mode plan --model gemini-3.8-flash-high --print-timeout 15m > "$D/review_gemini.md"
```

- [ ] **Step 3: Verify the skill loads:** in a new session the skill list shows `delegate`.

### Task 2: End-to-end check on a throwaway repo

- [ ] **Step 1: Set up the repo.** In `$TEMP/delegate/e2e`, `git init` a repo containing `slug.py` (empty).
- [ ] **Step 2: Have Codex write the code.** Following the skill, Codex implements `slugify(text: str) -> str` plus `test_slug.py`.
  - **Expected:** files exist, and `python -m pytest -q` passes.
- [ ] **Step 3: Run both goldfish in parallel on `git diff`.**
  - **Expected:** both files contain only findings in the 🔴/🟡/🟢 + `file:line` format, or the literal `NO FINDINGS`.
- [ ] **Step 4: Triage and report.** Claude sorts the findings into FIX/CONTEST and reports the round result to the user.
