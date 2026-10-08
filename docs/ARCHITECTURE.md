# Architecture

```
harness logs (read-only)          local receipt store           outputs
────────────────────────          ───────────────────           ───────
~/.claude/projects/*/*.jsonl
~/.codex/sessions/**/*.jsonl  ──┐
~/.gemini/tmp/**/chats/*.{json,jsonl} │
        │  parsers (ingest/)      │   SQLite, hash-chained
        │  resume via file offsets│   ┌──────────────┐
        ▼                         │  │ events, spend │──► dashcam today (digest + anomaly flags)
  dashcam ingest ──atomic/file──► │  │ sessions      │──► dashcam runs
                                  │  │ meta (offsets,│──► dashcam verify
                                  │  │  chain head)  │──► dashcam export (JSON / HEC / signed bundle)
                                  └──────────────┘         └──► dashcam verify-bundle (offline)
```

## The chain

Each evidentiary row seals the previous one:

```
hash_n = sha256(hash_{n-1} + canonical_json({kind, seq, ...payload}))
```

- `seq` is a global monotonic counter across *both* tables, so verify
  recomputes rows in exactly the order they were sealed — interleaved
  event/spend writes verify correctly.
- `canonical_json` is `json.dumps(sort_keys=True, separators=(",",":"))`.
- Genesis is 64 zeroes. The head hash and seq live in `meta`.
- `verify()` recomputes every row and compares both the per-row hash and
  the stored head. Any modification or deletion is detected.

`sessions` is index metadata (id, harness, project, timestamps) and is
not chained; the *evidence* — what the agent did and what it cost — is.

## Ingestion

- Parsers translate one harness's log format into normalized
  `Session` / `ToolEvent` / `Spend` records. See `ingest/base.py`.
- Resume: per-file line offsets are stored in `meta` (`file:<path>`), so
  re-running `ingest` only reads new lines. Sessions are idempotent
  (`INSERT OR IGNORE` + offset tracking); re-ingestion never duplicates.
- Malformed lines are skipped, never fatal. A parser must be total.
- Each file's writes happen inside `Store.atomic()`: one ACID
  transaction per file.
- Summaries are truncated and passed through `redact()` before storage.

## Cost

`src/dashcam/data/models.json` maps `provider/model` → USD per 1M input/output
tokens. Cost is computed at ingest from transcript token counts — no
model calls, no proxy. Unknown models record `$0` and warn; price updates
are data-only PRs touching just that file.

## Adding a harness parser

1. Read `ingest/base.py` — implement `HARNESS`, `iter_transcripts`,
   `parse_transcript`.
2. **Verify the format against real harness output.** A parser whose
   format can't be confirmed against the actual harness does not ship:
   wrong receipts are worse than missing ones.
3. Add fixtures under `tests/fixtures/<harness>/` copied from real
   (sanitized) logs, and tests covering: parsing, malformed lines,
   incremental resume, dedup on re-ingest.
4. Register it in `cli.PARSERS`.

## Concurrency

WAL mode + `BEGIN IMMEDIATE` on every read-modify-write + re-entrant
thread lock + 30s busy timeout. Two `dashcam ingest` processes (or two
agents' harnesses writing logs while dashcam reads) don't corrupt the
store and can't fork the chain.

## What this is not

No firewall, no blocking, no prompt-injection judge, no network. The
narrowness is the design: every feature must keep the receipt layer
trustworthy, or it doesn't ship.

## Anomaly surfacing (`anomaly.py`)

Explainable statistics, no ML. For each (project, harness) pair, the
trailing-30-day median per-session cost and tool-call count form the
baseline (median is robust to the outliers being hunted). A session is
flagged when it exceeds 3x the median on cost (floor: $1) or tool calls
(floor: 10), and only when at least 5 baseline sessions exist — so new
projects don't cry wolf. `dashcam today` shows the flags with the
baseline numbers that triggered them.

## Signed bundles (`signing.py`)

The hash chain proves the local DB wasn't altered in place, but it can't
leave the machine. `dashcam init` creates a local Ed25519 keypair
(`~/.dashcam/signing.key`, mode 0600). `export --format bundle --session
<id>` emits the session's canonical receipts plus signature and public
key; `verify-bundle` checks the signature offline with no database
access. This proves *provenance* — these receipts came from this dashcam
identity, unaltered since signing — not protection against an attacker
who owned the machine before signing. Keep offline copies of bundles
for that threat.
