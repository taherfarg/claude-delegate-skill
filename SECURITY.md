# Security policy

`delegate` runs other AI tools on your code, so these guarantees matter. If you find a way around any of them, please report it.

## What delegate guarantees

- **No sandbox bypass flags, ever:** it never passes `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`, `--yolo`, or `-s danger-full-access`.
- **The Codex worker is confined:** it runs with `-s workspace-write`. Reviewers are read-only (`codex -s read-only`, `agy --mode plan`).
- **Secrets are checked first:** a secret scan runs before any diff or brief leaves the machine, and any hit blocks the send.
- **Your own edits stay local:** uncommitted edits you had before a task are never sent to reviewers.
- **Approval is human-only:** `delegate approve` requires a real interactive console. It refuses piped input and the Windows `NUL` device.
- **Workers can't fake evidence:** a worker can't forge other tasks' state in `.delegate/` (it is snapshotted and restored), and editing `delegate.toml` or test/lint config makes a task high-risk.
- **Metrics hold counts only:** never code, finding text, or briefs.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** (the "Report a vulnerability" button on this repo's **Security** tab) rather than a public issue. Include:
- what you did
- what `delegate` allowed that it shouldn't have
- the smallest repro you can make

You'll get a reply within a few days.

## Supported versions

| Version | Supported |
|---|---|
| 2.x | ✅ |
| 1.x (prompt-only skill) | ❌ (no enforcement to fix; upgrade to 2.x) |
