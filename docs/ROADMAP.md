# dashcam roadmap

Principle: **narrow beats bundle**. Each release does one thing well. No feature
ships until the receipt layer beneath it is solid.

## v0.1 — the tape (current)
- Claude Code transcript ingestion (read-only, resumable)
- Hash-chained SQLite receipts + `dashcam verify`
- Cross-provider cost from `pricing/models.json`
- `dashcam today` digest, `dashcam runs`, `dashcam demo`
- CI (ruff + pytest on 3.10–3.13), PyPI trusted publishing with Sigstore attestations

## v0.2 — more harnesses, more time
- opencode session parser
- Gemini CLI log parser
- `dashcam serve`: localhost-only read-only timeline UI (single HTML file, stdlib server)
- Per-project digests (`dashcam today --project foo`)

## v0.3 — optional brains (all off by default)
- BYOK digest summarizer: natural-language "what did my agents do" via the user's
  own key through a provider abstraction (LiteLLM-style). Deterministic receipts
  stay the source of truth; the model only narrates.
- Anomaly surfacing: sessions whose cost or tool-call counts deviate from baseline

## Non-goals (will not build)
- Pre-execution blocking / firewalls — bypassable, fatiguing, uninstall-bait
- Cloud sync, accounts, telemetry of any kind
- LLM-as-judge verdicts on agent behavior — evidence in, verdicts out
