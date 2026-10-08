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

## v0.2 — universal tape (current)
- Codex CLI + Gemini CLI parsers (verified against documented formats);
  `dashcam ingest` reads all harnesses, `--harness` selects one
- Unusual-session flags in `dashcam today` (median-baseline anomaly surfacing)
- Ed25519 signed bundles: `export --format bundle` + `verify-bundle`
  (offline-verifiable receipts for auditors)

## v0.3 — more harnesses, more surface
- opencode session parser (SQLite source)
- `dashcam serve`: localhost-only read-only timeline UI (single HTML file, stdlib server)
- Per-project digests (`dashcam today --project foo`)

## v0.4 — optional brains (all off by default)
- BYOK digest summarizer: natural-language "what did my agents do" via the user's
  own key through a provider abstraction (LiteLLM-style). Deterministic receipts
  stay the source of truth; the model only narrates.

## Non-goals (will not build)
- Pre-execution blocking / firewalls — bypassable, fatiguing, uninstall-bait
- Cloud sync, accounts, telemetry of any kind
- LLM-as-judge verdicts on agent behavior — evidence in, verdicts out
