# Contributing

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install pytest ruff
pytest -q && ruff check src tests
```

CI runs the same on Python 3.10–3.13. Keep it green.

## Ground rules

- **Deterministic, read-only, tested.** Parsers never modify harness logs.
- **Narrow beats bundle.** If a feature doesn't make the receipts more
  trustworthy or more useful, it probably doesn't belong.
- Every parser change needs fixture transcripts under
  `tests/fixtures/<harness>/` and tests for: parsing, malformed input,
  incremental resume, no-duplicate re-ingest.
- Price updates touch **only** `pricing/models.json`.
- Never persist full prompts. Run new tool-input shapes through
  `redact()` and add a test proving secrets don't land in SQLite.
- Don't weaken `store.py`'s transaction discipline, WAL mode, or file
  permissions without discussion.

## Adding a harness

See `docs/ARCHITECTURE.md`. The hard requirement: verify the log format
against real harness output. An unverified parser doesn't ship.

## Commits & PRs

- Small, focused commits with clear messages.
- PRs: describe what changed, what you verified, and how (tests, manual
  runs). Link fixtures to their source format.
- Be ready to defend every line — this project holds people's receipts.
