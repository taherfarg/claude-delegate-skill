# delegate: a multi-agent skill for Claude Code

Claude Code acts as the **elephant**: it plans the work, writes the briefs and makes the final calls. It delegates to the AI subscriptions you already pay for:

- **ChatGPT, via the [Codex CLI](https://github.com/openai/codex):** writes code (features, tests, fixes).
- **Gemini, via the Antigravity CLI (`agy`):** research, codebase scans and reviews.

Both act as **goldfish reviewers**: fresh-context second opinions that never see the conversation, so they catch assumptions Claude can't see anymore. The idea comes from [google/elephant-goldfish-model](https://github.com/google/elephant-goldfish-model).

**Goals:** save Claude usage limits, get a second opinion and get work done faster, without API keys.

## How it works

1. Claude splits the work and writes a short brief per task.
2. Codex implements it in a sandbox. Several workers can run in parallel, each in its own git worktree.
3. Claude checks the diff and runs the tests itself.
4. Codex and Gemini review the diff in parallel and return findings as 🔴 Blocker / 🟡 Major / 🟢 Nit with `file:line`.
5. Claude marks every finding FIX or CONTEST, fixes test-first, then runs the review again. It stops after one clean round on the latest diff, or after 3 rounds at most, when you decide what's still open.

## Requirements

- [Claude Code](https://claude.com/claude-code)
- Codex CLI, logged in with ChatGPT: `codex login`. Check with `codex login status`.
- Antigravity CLI (`agy`), logged in with your Google account. Check with `agy models`.
  - The old `gemini` CLI no longer accepts personal Google logins, so use `agy`.
- `git`. Tested on Windows 11 with Git Bash.

## Install

macOS / Linux / Git Bash:

```bash
git clone https://github.com/taherfarg/claude-delegate-skill ~/.claude/skills/delegate
```

PowerShell:

```powershell
git clone https://github.com/taherfarg/claude-delegate-skill "$env:USERPROFILE\.claude\skills\delegate"
```

Start a new Claude Code session; `delegate` will show up in the skill list. To update later, run `git pull` in that folder.

## Use

Build as usual. The skill kicks in for multi-step work, or you can ask directly:

- "use codex to implement this"
- "get a second opinion from gemini and codex"
- "scan the codebase with gemini and tell me how auth works"

## Safety

- Codex runs with `-s workspace-write`, and `agy` runs read-only (`--mode plan`).
- Sandbox-bypass and yolo flags are never used.
- Briefs and diffs are limited to the task's files and never contain secrets.
- Worker output is treated as untrusted data.

## License

MIT
