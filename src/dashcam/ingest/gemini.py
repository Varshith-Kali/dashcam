"""Read-only parser for Gemini CLI chat logs.

Source: ``${GEMINI_DATA_DIR:-~/.gemini}/tmp/**/chats/*.{json,jsonl}`` — the
harness's own record. Files are JSON arrays/objects or JSONL; each record
carries some of:

- ``sessionId`` / ``session_id`` — session identity
- ``model`` — model name
- ``timestamp`` / ``createdAt`` / ``startTime`` / ``lastUpdated``
- ``stats`` / ``tokens`` / ``result.stats`` — token counts with keys like
  ``input``/``input_tokens``, ``output``/``output_tokens``, ``thoughts``,
  ``tool``/``tool_tokens``, ``cached``/``cached_tokens``
- ``messages`` — array of messages; model messages carry ``parts`` with
  ``functionCall: {name, args}`` entries (Gemini API shape)

Format reference: ccusage's ``rust/adapters/gemini/src/parser.rs``
(``GeminiRecord`` field names and the stats-key aliases). Records that
match none of these shapes are skipped, never fatal.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .. import redact as redact_mod
from ..models import Session, Spend, ToolEvent

HARNESS = "gemini"

INPUT_KEYS = ("input_tokens", "input", "prompt_tokens", "promptTokenCount")
OUTPUT_KEYS = ("output_tokens", "output", "completion_tokens", "candidatesTokenCount")
CACHED_KEYS = ("cached_tokens", "cached", "cache_read", "cachedContentTokenCount")
TS_KEYS = ("timestamp", "createdAt", "created_at", "startTime", "lastUpdated")


def gemini_data_dir() -> Path:
    return Path(os.environ.get("GEMINI_DATA_DIR", Path.home() / ".gemini"))


def transcript_dirs(base: Path | None = None) -> list[Path]:
    root = base or gemini_data_dir()
    tmp = root / "tmp"
    if tmp.is_dir():
        return [tmp]
    return [root] if root.is_dir() else []


def iter_transcripts(base: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Yield (project, path) for every Gemini chat file."""
    for d in transcript_dirs(base):
        files = sorted(d.rglob("chats/*.json")) + sorted(d.rglob("chats/*.jsonl"))
        for f in files:
            yield "gemini", f


def _tokens(v) -> int:
    try:
        n = int(float(v or 0))
    except (TypeError, ValueError):
        return 0
    return max(n, 0)


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _stats_tokens(stats: Any) -> tuple[int, int, int]:
    """(input, output, cached) from a stats-like mapping."""
    if not isinstance(stats, dict):
        return 0, 0, 0
    in_tok = _tokens(_first(stats, INPUT_KEYS))
    out_tok = _tokens(_first(stats, OUTPUT_KEYS))
    cached = _tokens(_first(stats, CACHED_KEYS))
    return in_tok, out_tok, cached


def _record_tokens(rec: dict) -> tuple[int, int, int]:
    stats = rec.get("stats")
    if stats is None:
        result = rec.get("result")
        if isinstance(result, dict):
            stats = result.get("stats")
    if stats is None:
        stats = rec.get("tokens")
    return _stats_tokens(stats)


def _record_ts(rec: dict) -> str | None:
    v = _first(rec, TS_KEYS)
    return v if isinstance(v, str) and v else None


def _iter_function_calls(messages: Any) -> Iterator[tuple[str, str]]:
    """Yield (name, args_json) for functionCall parts in a messages array."""
    if not isinstance(messages, list):
        return
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        parts = msg.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            fc = part.get("functionCall")
            if not isinstance(fc, dict):
                continue
            name = fc.get("name")
            if isinstance(name, str) and name:
                args = fc.get("args", {})
                yield name, json.dumps(args)[:500] if isinstance(args, dict) else str(args)[:500]


def _load_records(path: Path) -> list[dict]:
    """Read a chat file as JSONL or whole-file JSON; return record dicts."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    records: list[dict] = []
    # Try whole-file JSON first (arrays and single objects).
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, list):
        return [r for r in whole if isinstance(r, dict)]
    if isinstance(whole, dict):
        # A single chat object: may itself be the record, or wrap messages.
        return [whole]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def parse_transcript(project: str, path: Path, skip_lines: int = 0):
    """Parse one Gemini chat file.

    Returns (session, events, spends, nlines). Malformed content is skipped,
    never fatal.
    """
    session_id = path.stem
    model = "gemini-unknown"
    started_at: str | None = None
    ended_at: str | None = None
    events: list[ToolEvent] = []
    spends: list[Spend] = []

    records = _load_records(path)
    nlines = len(records)
    for rec in records[skip_lines:]:
        sid = rec.get("sessionId") or rec.get("session_id")
        if isinstance(sid, str) and sid:
            session_id = sid
        m = rec.get("model")
        if isinstance(m, str) and m:
            model = m
        ts = _record_ts(rec)
        if ts:
            started_at = started_at or ts
            ended_at = ts

        in_tok, out_tok, _cached = _record_tokens(rec)
        if in_tok or out_tok:
            spends.append(Spend(session_id=session_id, provider="google",
                                model=model, input_tokens=in_tok,
                                output_tokens=out_tok))

        for name, args_json in _iter_function_calls(rec.get("messages")):
            summary = f"{name}({args_json[:160]})" if args_json else name
            events.append(ToolEvent(session_id=session_id, ts=ts, tool=name,
                                    summary=redact_mod.redact(summary),
                                    detail=redact_mod.redact(
                                        json.dumps({"args": args_json[:500]}))))

    session = Session(id=session_id, harness=HARNESS, project=project or "gemini",
                      started_at=started_at, ended_at=ended_at)
    return session, events, spends, nlines
