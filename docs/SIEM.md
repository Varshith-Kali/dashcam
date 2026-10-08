# dashcam × your SIEM

## Why you need this if you already run Splunk

Short version: **your SIEM doesn't see what your agents do.**

- Agent transcripts live on developer laptops (`~/.claude/projects/*/*.jsonl`).
  Nobody forwards them — Splunk ingest is priced per GB, and agent chatter
  is high-volume, low-signal for the SOC. Paying ingest pricing for
  "agent ran `ls` 400 times" is not a trade anyone makes.
- What the SIEM *does* see (EDR process events, proxy logs) shows that
  *something* ran, not the agent's tool-call sequence: which files it
  read, what commands it executed, which models it used, what it cost.
- dashcam is the **edge sensor**: local-first, zero-config, free. It
  records the tool-call tape where the work happens. Forward to your SIEM
  only what you want, when you want it.

Splunk's own AI-observability push (tokenomics tracking, 2026) is
top-down and enterprise-priced. dashcam is bottom-up: the developer gets
the tape in 60 seconds, the SOC gets clean JSONL whenever they're ready.

## Forwarding to Splunk HEC

```bash
# One-shot backfill of today's receipts:
dashcam export --day 2026-10-08 --format hec | \
  curl -s -H "Authorization: Splunk $HEC_TOKEN" \
       -H "Content-Type: application/json" \
       --data-binary @- \
       https://your-splunk:8088/services/collector/event

# Or schedule it (cron, every 15 min — export is idempotent, dedupe on chain_seq):
*/15 * * * * dashcam export --format hec | curl -s -H "Authorization: Splunk $HEC_TOKEN" --data-binary @- https://your-splunk:8088/services/collector/event
```

Each line is a self-contained HEC event:

```json
{"time":1728390000.0,"host":"laptop-7","source":"dashcam","sourcetype":"dashcam:receipt",
 "event":{"kind":"tool_call","session_id":"…","harness":"claude-code","project":"…",
          "tool":"Bash","summary":"$ pytest -q","chain_seq":12,"chain_hash":"…"}}
```

Suggested Splunk config: `sourcetype=dashcam:receipt`, index on
`event.chain_seq` for dedup, `event.session_id` for session drill-down.
`chain_hash` lets an auditor re-verify the tape with `dashcam verify`
against the local database.

The same `--format hec` lines work for any HEC-compatible receiver
(Elastic, Cribl, Panther, Datadog HTTP intake with minor reshaping).

## Compliance mapping

Regulators increasingly ask for exactly this artifact:

| Requirement | dashcam field |
|---|---|
| EU AI Act Art. 12 — automatic event logging | hash-chained `events` table, `dashcam verify` |
| Art. 12 — tamper-evident logs | SHA-256 chain (`chain_seq`, `chain_hash`) |
| Art. 26(6) — 6-month retention | local SQLite; retention policy is yours |
| SOC 2 CC7 — detect & review events | `dashcam today`, `dashcam runs` |
| Tool invocation: what / when / with what | `tool`, `summary`, `ts`, `session_id` |
| Model accountability: which model, how much | `provider`, `model`, token counts, `cost_usd` |

Caveats, stated plainly: the chain is tamper-evident, not tamper-proof
(see SECURITY.md); agent identity here is the harness session, not a
corporate identity — join on `host` + your own asset inventory for
attribution. dashcam is one strong input to an audit story, not the
whole story.
