# dashcam roadmap

Principle: **depth on the core thesis** — the universal, intelligent,
undeniable tape. A feature ships only if it makes the receipts more
universal, more intelligent, or more portable.

## v0.1 — the tape (shipped)
- Claude Code transcript ingestion (read-only, resumable)
- Hash-chained SQLite receipts + `dashcam verify`
- Cross-provider cost from `src/dashcam/data/models.json`
- `dashcam today` digest, `dashcam runs`, `dashcam demo`
- Splunk HEC export (`docs/SIEM.md`)
- CI (ruff + pytest on 3.10–3.13), PyPI trusted publishing with Sigstore attestations

## v0.2 — universal tape (shipped)
- Codex CLI + Gemini CLI parsers (verified against documented formats);
  `dashcam ingest` reads all harnesses, `--harness` selects one
- Unusual-session flags in `dashcam today` (median-baseline anomaly surfacing)
- Ed25519 signed bundles: `export --format bundle` + `verify-bundle`
  (offline-verifiable receipts for auditors)

## v0.3 — more harnesses, more surface (shipped)
- opencode session parser (SQLite source, read-only, fork-deduped)
- `dashcam serve`: localhost-only read-only timeline UI (stdlib only)
- Per-project digests (`dashcam today --project foo`)

## v0.4 — optional brains (shipped, all off by default)
- `dashcam brief`: BYOK narration of the digest via your own key
  (OpenAI-compatible or Anthropic, stdlib HTTP). Deterministic receipts
  stay the source of truth; the model only narrates. Only aggregate
  stats are ever sent — never prompts or tool output.

## What's next

The planned roadmap is complete. Further work is driven by real usage:
file an issue describing what your agents do that dashcam doesn't
capture yet.

## Non-goals (will not build)
- Pre-execution blocking / firewalls — bypassable, fatiguing, uninstall-bait
- Cloud sync, accounts, telemetry of any kind
- LLM-as-judge verdicts on agent behavior — evidence in, verdicts out
