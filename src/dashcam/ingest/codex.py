"""Read-only parser for Codex CLI rollout logs.

Source: ``${CODEX_HOME:-~/.codex}/sessions/**/*.jsonl`` (and
``archived_sessions/``) — the harness's own record. Each line is a JSON
object; the shapes dashcam reads are:

- ``{"type": "session_meta", "payload": {...}}`` — session identity
  (id, cwd, model).
- ``{"type": "event_msg", "payload": {"type": "token_count",
  "info": {"last_token_usage": {"input_tokens": n, "output_tokens": m}}}}``
  — per-turn token deltas.
- ``{"type": "response_item", "payload": {"type": "function_call",
  "name": "...", "arguments": "..."}}`` — tool invocations, following the
  OpenAI Responses API item shape Codex persists.

Format references: ccusage's ``rust/adapters/codex/src/README.md``
(documents the ``token_count`` / ``last_token_usage`` contract and the
sessions directory layout) and the Codex protocol crate
(``codex-rs/protocol/src/items.rs``) for response-item shapes.
Anything not matching these shapes is skipped, never fatal.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from .. import redact as redact_mod
from ..models import Session, Spend, ToolEvent

HARNESS = "codex"

# Model fallback mirrors ccusage: early sessions without metadata resolve
# to gpt-5 and are marked approximate via the "unknown" provider path.
FALLBACK_MODEL = "gpt-5"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def transcript_dirs(base: Path | None = None) -> list[Path]:
    root = base or codex_home()
    dirs = [root / "sessions", root / "archived_sessions"]
    return sorted(d for d in dirs if d.is_dir())


def iter_transcripts(base: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Yield (project, path) for every Codex rollout file."""
    for d in transcript_dirs(base):
        for f in sorted(d.rglob("*.jsonl")):
            yield "codex", f


def _tokens(v) -> int:
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        return 0
    return max(n, 0)


def _summarize_function_call(name: str, arguments) -> str:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
    args = args.strip()[:160]
    return f"{name}({args})" if args else name


def parse_transcript(project: str, path: Path, skip_lines: int = 0):
    """Parse one Codex rollout file.

    Returns (session, events, spends, nlines). Malformed lines and unknown
    record shapes are skipped, never fatal.
    """
    session_id = path.stem
    cwd: str | None = None
    model = FALLBACK_MODEL
    started_at: str | None = None
    ended_at: str | None = None
    events: list[ToolEvent] = []
    spends: list[Spend] = []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines[skip_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        ts = rec.get("timestamp")
        if isinstance(ts, str) and ts:
            started_at = started_at or ts
            ended_at = ts
        rtype = rec.get("type")
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue

        if rtype == "session_meta":
            sid = payload.get("id")
            if isinstance(sid, str) and sid:
                session_id = sid
            c = payload.get("cwd")
            if isinstance(c, str) and c:
                cwd = c
            m = payload.get("model")
            if isinstance(m, str) and m:
                model = m
            continue

        if rtype == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            last = info.get("last_token_usage")
            if not isinstance(last, dict):
                continue
            in_tok = _tokens(last.get("input_tokens"))
            out_tok = _tokens(last.get("output_tokens"))
            if in_tok or out_tok:
                spends.append(Spend(session_id=session_id, provider="openai",
                                    model=model, input_tokens=in_tok,
                                    output_tokens=out_tok))
            continue

        if rtype == "response_item" and payload.get("type") == "function_call":
            name = payload.get("name")
            if not isinstance(name, str) or not name:
                continue
            summary = _summarize_function_call(name, payload.get("arguments"))
            events.append(ToolEvent(session_id=session_id, ts=ts if isinstance(ts, str) else None,
                                    tool=name, summary=redact_mod.redact(summary),
                                    detail=redact_mod.redact(json.dumps(
                                        {"arguments": str(payload.get("arguments", ""))[:500]}))))
            continue

    session = Session(id=session_id, harness=HARNESS,
                      project=cwd or project or "codex",
                      started_at=started_at, ended_at=ended_at)
    return session, events, spends, len(lines)
