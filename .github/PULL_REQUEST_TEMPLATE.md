## What and why

<!-- One paragraph: what changes, and why it is needed. Link the roadmap item / issue if any. -->

## Files

<!-- Which files change and what each does. Keep PRs single-topic. -->

## How to test

```bash
# e.g. .venv/bin/pytest -q, ultraai config list, ...
```

## Checklist

- [ ] `pytest` green, new tests included where behavior changed
- [ ] Lint clean (ruff)
- [ ] Docs updated (README / PLAN.md / EDITOR_PLAN.md / CONTRIBUTING.md / docstrings)
- [ ] Pipeline change: `compare_runs.py` numbers in this description + new PLAN.md progress entry, no regression on the 35-song benchmark
- [ ] `ultraai/studio/` still imports no heavy pipeline modules (base install only)
- [ ] CI green (tests, pip-audit, CodeQL, gitleaks)

## Notes for reviewers

<!-- Anything surprising: trade-offs, things deliberately left out, follow-ups. -->
