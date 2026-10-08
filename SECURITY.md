# Security policy

## What dashcam is

A read-only, local-first flight recorder. It parses the session logs your
coding agents already write, keeps hash-chained receipts in SQLite, and
never sends anything anywhere. There is no network code in this project —
no telemetry, no accounts, no cloud sync, no update checks.

## Threat model

**In scope:**

- *Post-hoc tampering with receipts.* Every evidentiary row (tool call,
  spend) is sealed into a SHA-256 hash chain in global insertion order.
  `dashcam verify` recomputes the chain; altering or deleting a row breaks
  it. This catches accidental corruption (crashes, bit rot, partial
  writes) and unsophisticated tampering.
- *Secret leakage into receipts.* Command text and tool inputs pass
  through deterministic redaction (`src/dashcam/redact.py`) before they
  are persisted, and text is truncated. Full prompts are never stored.
- *Local file exposure.* The database directory is created `0700` and the
  database `0600` (POSIX). Receipts can contain command text — they are
  not world-readable.
- *Concurrent writers.* SQLite WAL mode, `BEGIN IMMEDIATE` around every
  read-modify-write (so two processes can't fork the chain), a re-entrant
  lock for threads, 30s busy timeout. Per-file ingestion runs inside one
  ACID transaction: a crash mid-file rolls back instead of leaving half a
  transcript.

**Out of scope — stated plainly:**

- The chain is **tamper-evident, not tamper-proof**. It is not keyed: an
  attacker with unrestricted write access to `receipts.db` *and* the
  knowledge to recompute the chain could forge a valid-looking history.
  What the chain guarantees is *detection* of any modification by parties
  who can't or don't rebuild it, plus all accidental corruption. If you
  need proof against a fully-privileged local attacker, keep independent
  copies of the database (backups detect divergence) — and see the
  roadmap note on keyed receipts below.
- dashcam does not sandbox, block, or restrain your agents. It is a
  recorder. A compromised agent is outside this project's scope to stop;
  the point is that the tape survives to show what happened.
- Redaction is best-effort pattern matching, not a guarantee. Exotic
  secret formats will slip through. Treat receipts as sensitive and keep
  the `0600` permissions intact.

**Roadmap (not yet built):** keyed receipts — HMAC-SHA256 with the key
held in the OS keychain, so forging the chain requires keychain access
too. This is the honest upgrade path from tamper-evident to tamper-proof.

## Reporting a vulnerability

Open a GitHub issue titled `[security]` with the details. Do not include
secrets, tokens, or private data in the report. This is a young project;
response time is best-effort but reports are taken seriously.

## For contributors

- Parsers must be read-only: never modify the harness logs.
- Never persist full prompts, and run new tool-input shapes through
  `redact()` — add a test in `tests/` proving the secret doesn't land in
  the database.
- Don't weaken file permissions, WAL mode, or the transaction discipline
  in `store.py` without discussion in the PR.
