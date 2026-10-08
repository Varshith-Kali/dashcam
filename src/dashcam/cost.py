"""Cross-provider cost computation.

Prices live in ``pricing/models.json`` (USD per 1M tokens) and are deliberately
a plain data file: when providers change prices, anyone can send a PR that only
touches data. No model calls are ever needed to compute cost — the harness
transcripts already record token usage.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PRICING_PATH = Path(__file__).resolve().parent.parent.parent / "pricing" / "models.json"


def load_pricing(path: Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_PRICING_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_key(provider: str, model: str) -> str:
    return f"{(provider or '').lower().strip()}/{(model or '').lower().strip()}"


def lookup_rate(provider: str, model: str, pricing: dict | None = None) -> tuple[float, float]:
    """Return (input_usd_per_1m, output_usd_per_1m). Unknown models cost $0 and are flagged."""
    pricing = pricing if pricing is not None else load_pricing()
    key = normalize_key(provider, model)
    if key in pricing:
        r = pricing[key]
        return float(r["input"]), float(r["output"])
    # Fall back to any entry whose model part matches (provider unknown / renamed).
    m = (model or "").lower().strip()
    for k, r in pricing.items():
        if k.split("/", 1)[-1] == m:
            return float(r["input"]), float(r["output"])
    return 0.0, 0.0


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int,
             pricing: dict | None = None) -> tuple[float, bool]:
    """Return (cost_usd, priced_known). priced_known=False means the model wasn't in the table."""
    ri, ro = lookup_rate(provider, model, pricing)
    known = (ri, ro) != (0.0, 0.0)
    return (input_tokens / 1_000_000) * ri + (output_tokens / 1_000_000) * ro, known
