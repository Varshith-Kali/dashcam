"""Best-effort secret redaction for persisted receipts.

Receipts store truncated command text and tool inputs so you can see what
your agents *did*. That text can contain secrets (a pasted API key, a
``--password`` flag). Before anything is written to SQLite, it passes
through :func:`redact`.

This is deliberately deterministic and offline: a list of well-known
secret shapes, no ML, no network. It is best-effort — exotic secret
formats will slip through — which is why receipts also truncate text and
never store full prompts. See SECURITY.md.
"""
from __future__ import annotations

import re

_PATTERNS = [
    # Cloud / provider keys with recognizable prefixes.
    re.compile(r"AKIA[0-9A-Z]{16}"),                       # AWS access key id
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                    # GitHub classic PAT
    re.compile(r"gho_[A-Za-z0-9]{36}"),                    # GitHub OAuth
    re.compile(r"ghu_[A-Za-z0-9]{36}"),
    re.compile(r"ghs_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),            # GitHub fine-grained PAT
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                  # OpenAI-style secret keys
    re.compile(r"xox[bpras]-[A-Za-z0-9-]+"),               # Slack tokens
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),  # PEM private keys
    # Bearer tokens in headers / curl commands.
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    # key=value / key: value shapes (passwords, tokens, api keys).
    re.compile(
        r"(?i)(password|passwd|pwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token)"
        r"\s*[:=]\s*['\"]?[^\s'\"]+['\"]?"
    ),
    # --flag=value shapes.
    re.compile(r"(?i)(--?(?:password|passwd|pwd|token|api[_-]?key|secret))[ =]['\"]?[^\s'\"]+['\"]?"),
]

REDACTED = "[REDACTED]"


def redact(text: str | None) -> str:
    """Replace recognizable secrets in *text* with [REDACTED]."""
    if not text:
        return ""
    out = text
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out
