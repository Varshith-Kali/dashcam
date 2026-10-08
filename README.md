# dashcam

**The dashcam for your AI coding agents.**

A dashcam doesn't stop the car. It records. dashcam reads the session logs your coding agents *already write* and keeps **tamper-evident receipts** of what they did — every tool call, every token, every dollar. **Read-only. Local-first. Nothing leaves your machine.**

```
$ pipx install dashcam
$ dashcam demo
$ dashcam ingest && dashcam today
```

## What it is

- **Session receipts** — every agent session parsed from the harness's own logs into a local SQLite store, each row hash-chained (`sha256(prev_hash + record)`), so you can prove later exactly what happened. `dashcam verify` checks the tape.
- **Cross-provider cost** — token usage from transcripts converted to USD per provider/model from a community-maintained price table. No proxy, no MITM, no behavior change.
- **`dashcam today`** — the daily digest: sessions, tool calls, files touched, tokens, and spend. The one command worth running every day.

## What it is not

- **Not a firewall.** dashcam never blocks, prompts, or restrains your agent. Blocking sidecars get bypassed by the agent, fatigue the user, and get uninstalled in anger — we chose the other side of that trade on purpose. The tape doesn't lie, and it doesn't nag.
- **Not a cloud service.** No accounts, no servers, no telemetry. Your receipts live in `~/.dashcam/receipts.db`.

## Quickstart

```bash
pipx install dashcam        # or: pip install dashcam
dashcam demo               # 60-second tour with synthetic data — the first run is never empty
dashcam ingest             # read your real Claude Code session logs (read-only)
dashcam today              # what did my agents do today?
dashcam verify             # prove the receipts weren't tampered with
dashcam runs               # list recorded sessions
```

## How it works

```
Claude Code transcripts (~/.claude/projects/*/*.jsonl)
        │  read-only parse (nothing is modified)
        ▼
  dashcam ingest ──► SQLite receipts.db ──► dashcam today
  (hash-chained)      (~/.dashcam/)          (rich digest)
```

Each recorded row seals the previous one into a SHA-256 chain. Delete or alter a row and `dashcam verify` fails — the same tamper-evidence idea behind signed audit logs, running entirely on your laptop.

## Cost data

Prices live in [`pricing/models.json`](pricing/models.json) (USD per 1M tokens). Providers change prices; when they do, send a PR that touches *only* that file.

## Roadmap

- **v0.1** — Claude Code transcripts, hash-chained receipts, cost, digest, demo, verify (this release)
- **v0.2** — opencode + Gemini CLI parsers, `dashcam serve` (localhost read-only timeline)
- **v0.3** — optional BYOK digest summarizer (your own key via a provider abstraction, off by default)

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for detail.

## Honest comparisons

- **vs. blocking sidecars** (pre-execution firewalls): they restrain, we record. Restraint gets bypassed and fatigued; recording can't be bypassed and never nags. Different jobs.
- **vs. session scorers**: they grade your agent's *quality*; we keep the *evidence*. The tape comes before the verdict.

## Contributing

PRs welcome. The bar: deterministic, read-only, tested. Every parser change needs fixture transcripts in `tests/`. Price updates touch only `pricing/models.json`.

## License

MIT — see [LICENSE](LICENSE).
