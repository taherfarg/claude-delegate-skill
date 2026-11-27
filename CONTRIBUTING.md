# Contributing

Thanks for helping. This project has one firm rule: **the engine must never report success without evidence.** Every change is judged against that.

## Setup

```bash
git clone https://github.com/taherfarg/claude-delegate-skill
cd claude-delegate-skill
pip install -e ".[dev]"
python -m pytest -q
```

The runtime is **Python ≥ 3.11, standard library only**. pytest is a dev-only dependency. Please don't add runtime dependencies.

## How the tests work

The tests never call the real Codex or Gemini. `tests/conftest.py` puts fake `codex` and `agy` launchers on `PATH` (see `tests/fakes/fake_cli.py`). You choose each fake's behavior with environment variables:

| `FAKE_CODEX` / `FAKE_AGY` mode | Behaves like |
|---|---|
| `none` | a clean review (`NO FINDINGS`) |
| `findings` | two valid findings |
| `prose`, `empty` | invalid output (triggers the one retry) |
| `hang` | a stuck process with a child (tests the process-tree kill) |
| `quota` | a usage-limit error (`unavailable`) |
| `write:<path>:<text>` | a worker that writes a file |

Several modes separated by commas are used one per call, so `prose,none` means "fail once, then succeed".

## Making a change

1. **Write the failing test first.** For a bug, the test reproduces it, and you watch it fail before you fix anything.
2. **Keep changes to gates and risk rules conservative.** Anything in `delegate/gates.py` that could let `done` pass with less evidence needs a test showing exactly what it allows.
3. **Run the whole suite:** `python -m pytest -q`. CI runs it on Windows, Linux and macOS × Python 3.11–3.13.
4. **If you change the README numbers**, update `scripts/make_visuals.py` and regenerate the images with `python scripts/make_visuals.py`. Every number there must be real.

## Reporting bugs

Use the issue templates. For a real-CLI problem (Codex or `agy` behaving unexpectedly), include the last 20 lines of the log in `.delegate/tasks/<id>/` and your `codex --version` / `agy --version`. **Remove anything secret first.**

## Security

Please don't open public issues for security problems. See [SECURITY.md](SECURITY.md).
