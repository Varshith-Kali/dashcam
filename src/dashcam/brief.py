"""`dashcam brief` — optional BYOK narration of the deterministic digest.

Off by default; nothing calls a model unless you configure a key. When
configured, dashcam sends *aggregate statistics only* (session counts,
top tools/files, cost by model, anomaly flags) — never raw tool
summaries or prompt text — to your chosen provider, and prints a short
plain-English briefing. The receipts stay the source of truth; the model
only narrates.

Configuration (first found wins):
1. ``DASHCAM_API_KEY`` (+ optional ``DASHCAM_LLM_PROVIDER``,
   ``DASHCAM_LLM_MODEL``, ``DASHCAM_LLM_BASE_URL``)
2. ``~/.dashcam/llm.json`` (mode 0600):
   ``{"provider": "openai"|"anthropic", "api_key": "...",
      "model": "...", "base_url": "..."}``

The ``openai`` provider speaks OpenAI-compatible ``/chat/completions``,
so OpenRouter, Ollama, and other compatible endpoints work via
``base_url``. All HTTP uses stdlib only — no new dependencies.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SYSTEM_PROMPT = (
    "You are a concise engineering chief-of-staff. Below is a deterministic "
    "digest of what the user's AI coding agents did (session counts, tool "
    "usage, cost). Narrate it as a short briefing of ~120 words: what got "
    "done, what it cost, anything surprising. Use ONLY the numbers given — "
    "never invent figures. If an unusual session is flagged, call it out "
    "plainly."
)


@dataclass
class LLMConfig:
    provider: str  # openai | anthropic
    api_key: str
    model: str
    base_url: str | None = None


def config_path() -> Path:
    return Path.home() / ".dashcam" / "llm.json"


def load_config() -> LLMConfig | None:
    """Load BYOK config; None when not configured (feature stays off)."""
    api_key = os.environ.get("DASHCAM_API_KEY")
    provider = os.environ.get("DASHCAM_LLM_PROVIDER", "openai")
    model = os.environ.get("DASHCAM_LLM_MODEL", "")
    base_url = os.environ.get("DASHCAM_LLM_BASE_URL")
    if not api_key:
        p = config_path()
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            api_key = d.get("api_key")
            provider = d.get("provider", provider)
            model = d.get("model", model)
            base_url = d.get("base_url", base_url)
    if not api_key:
        return None
    provider = (provider or "openai").lower()
    if provider not in ("openai", "anthropic"):
        raise ValueError(f"unknown LLM provider '{provider}' (openai or anthropic)")
    if not model:
        model = {"openai": "gpt-5.6-luna",
                 "anthropic": "claude-haiku-4-5"}[provider]
    return LLMConfig(provider=provider, api_key=api_key, model=model,
                     base_url=base_url)


def save_config(cfg: LLMConfig) -> Path:
    """Write config with 0600 permissions. Used by `dashcam brief --setup`."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"provider": cfg.provider, "api_key": cfg.api_key,
                             "model": cfg.model, "base_url": cfg.base_url},
                            indent=2), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p


def build_brief_text(stats: dict) -> str:
    """Compact aggregate-only brief from digest stats. No raw summaries."""
    lines = [
        f"Day: {stats['day']}",
        (f"Sessions: {stats['sessions']}  "
         f"({', '.join(f'{k} {v}' for k, v in stats['by_harness'].items())})"),
        f"Tool calls: {stats['events']}",
        f"Tokens: {stats['total_in']:,} in / {stats['total_out']:,} out",
        f"Total cost: ${stats['total_cost']:.2f}",
    ]
    if stats["tools"]:
        top = ", ".join(f"{t}×{n}" for t, n in stats["tools"].most_common(6))
        lines.append(f"Top tools: {top}")
    if stats["by_model"]:
        costs = ", ".join(f"{k} ${b['cost']:.2f}"
                          for k, b in sorted(stats["by_model"].items(),
                                             key=lambda kv: -kv[1]["cost"])[:6])
        lines.append(f"Cost by model: {costs}")
    for f in (stats.get("flags") or [])[:5]:
        lines.append(f"UNUSUAL: session {f.session_id[:12]} "
                     f"({f.harness}/{f.project}) — {f.reason}")
    return "\n".join(lines)


def _post(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def narrate(cfg: LLMConfig, brief_text: str, timeout: int = 60) -> str:
    """Send the aggregate brief to the provider; return the narration."""
    if cfg.provider == "openai":
        base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        resp = _post(f"{base}/chat/completions",
                     {"Authorization": f"Bearer {cfg.api_key}"},
                     {"model": cfg.model, "temperature": 0.3, "max_tokens": 400,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": brief_text}]},
                     timeout)
        try:
            return resp["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected OpenAI response shape: {e}")
    else:  # anthropic
        base = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        resp = _post(f"{base}/v1/messages",
                     {"x-api-key": cfg.api_key,
                      "anthropic-version": "2023-06-01"},
                     {"model": cfg.model, "max_tokens": 400, "temperature": 0.3,
                      "system": SYSTEM_PROMPT,
                      "messages": [{"role": "user", "content": brief_text}]},
                     timeout)
        try:
            blocks = resp["content"]
            return "\n".join(b["text"] for b in blocks
                             if b.get("type") == "text").strip()
        except (KeyError, TypeError) as e:
            raise RuntimeError(f"unexpected Anthropic response shape: {e}")


SETUP_HINT = """dashcam brief needs your own API key (BYOK — nothing is sent anywhere until you set this up).

  export DASHCAM_API_KEY=sk-...                       # + optional DASHCAM_LLM_PROVIDER=openai|anthropic
  # or
  dashcam brief --setup   # writes ~/.dashcam/llm.json (0600)

Only aggregate stats (counts, costs) are ever sent — never your prompts or tool output."""
