## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] A test that failed before this change and passes after it
- [ ] `python -m pytest -q` passes locally
- [ ] No new runtime dependencies (stdlib only)
- [ ] If this touches `delegate/gates.py` or the risk rules: it can't let `done`/`approve` pass with less evidence (or the PR explains exactly what it allows and why)
- [ ] README numbers/visuals still real (`python scripts/make_visuals.py` if they changed)
