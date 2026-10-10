"""Read-only parser for opencode's SQLite session store.

Source: ``${OPENCODE_DATA_DIR:-~/.local/share/opencode}/opencode.db``
(also ``opencode-*.db``) — the harness's own record. Tables read:

- ``session`` / ``session_v2``: ``id, time_created, cost,
  tokens_input, tokens_output, tokens_cache_read, tokens_cache_write``
  (``session_v2`` additionally carries ``fork_session_id`` /
  ``fork_boundary`` for fork dedup).
- ``session_message``: ``id, session_id, type, data, time_created, seq`` —
  ``data`` is JSON with ``tokens: {input, output}``, ``modelID``,
  ``providerID``, ``cost``.

Coverage note: sessions and per-message spend are extracted from
verified shapes (see ccusage's ``rust/adapters/opencode`` loader/parser,
which reads these tables/columns from real databases). Tool-call
*parts* inside message ``data`` are NOT extracted yet — their exact
shapes couldn't be verified against real output, and an unverified
extraction is worse than none.

The database is opened read-only (``mode=ro``); dashcam never writes to
it. Incremental ingest reuses the file-offset mechanism: messages are
read in ``(time_created, id)`` order and the offset counts consumed
messages (opencode's message log is append-only).
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from ..models import Session, Spend

HARNESS = "opencode"


def data_dirs(base: Path | None = None) -> list[Path]:
    if base is not None:
        return [base] if base.is_dir() else []
    env = os.environ.get("OPENCODE_DATA_DIR")
    if env:
        p = Path(env)
        return [p] if p.is_dir() else []
    xdg = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    p = xdg / "opencode"
    return [p] if p.is_dir() else []


def transcript_dirs(base: Path | None = None) -> list[Path]:
    return data_dirs(base)


def iter_transcripts(base: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Yield (project, db_path) for every opencode database."""
    for d in data_dirs(base):
        dbs = sorted(d.glob("opencode.db")) + sorted(d.glob("opencode-*.db"))
        for db in dbs:
            yield "opencode", db


def _ro(db: Path) -> sqlite3.Connection | None:
    try:
        cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    cx.row_factory = sqlite3.Row
    try:
        cx.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        pass
    return cx


def _tables(cx: sqlite3.Connection) -> set[str]:
    try:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error:
        return set()
    return {r["name"] for r in rows}


def _col_names(cx: sqlite3.Connection, table: str) -> set[str]:
    try:
        cur = cx.execute(f"SELECT * FROM {table} LIMIT 0")
        return {d[0] for d in (cur.description or [])}
    except sqlite3.Error:
        return set()


def _ms_to_iso(ms) -> str | None:
    try:
        v = int(ms)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc).isoformat()


def _tokens(v) -> int:
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        return 0
    return max(n, 0)


def _session_rows(cx: sqlite3.Connection, tables: set[str]) -> list[dict]:
    """Session aggregates, preferring session_v2 (has fork metadata)."""
    table = "session_v2" if "session_v2" in tables else "session" if "session" in tables else None
    if table is None:
        return []
    cols = _col_names(cx, table)
    need = {"id", "time_created"}
    if not need.issubset(cols):
        return []
    extra = []
    for c in ("directory", "project", "title"):
        if c in cols:
            extra.append(c)
            break
    sel = "id, time_created" + ("".join(f", {c}" for c in extra))
    try:
        rows = cx.execute(f"SELECT {sel} FROM {table}").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        d = dict(r)
        proj = d.get(extra[0]) if extra else None
        out.append({"id": str(d["id"]),
                    "project": str(proj) if proj else "opencode",
                    "started_at": _ms_to_iso(d["time_created"])})
    return out


def _fork_cutoffs(cx: sqlite3.Connection, tables: set[str]) -> dict[str, int]:
    """session_id -> max seq to skip (rows the fork copied from its parent)."""
    if "session_v2" not in tables or "session_message" not in tables:
        return {}
    cols = _col_names(cx, "session_v2")
    if not {"id", "fork_session_id", "fork_boundary"}.issubset(cols):
        return {}
    msg_cols = _col_names(cx, "session_message")
    if not {"session_id", "seq", "id"}.issubset(msg_cols):
        return {}
    cutoffs: dict[str, int] = {}
    try:
        forks = cx.execute(
            "SELECT id, fork_session_id, fork_boundary FROM session_v2 "
            "WHERE fork_session_id IS NOT NULL").fetchall()
    except sqlite3.Error:
        return {}
    for f in forks:
        f = dict(f)
        parent, boundary = f["fork_session_id"], f["fork_boundary"]
        if not parent or boundary is None:
            continue
        try:
            row = cx.execute(
                "SELECT MAX(seq) AS m FROM session_message "
                "WHERE session_id = ? AND seq <= ?",
                (parent, int(boundary))).fetchone()
        except (sqlite3.Error, TypeError, ValueError):
            continue
        if row and row["m"] is not None:
            cutoffs[str(f["id"])] = int(row["m"])
    return cutoffs


def _message_spends(cx: sqlite3.Connection, tables: set[str],
                    cutoffs: dict[str, int],
                    skip: int) -> tuple[list[dict], int]:
    """(spend_dicts, total_messages) in (time_created, id) order after skip."""
    if "session_message" not in tables:
        return [], 0
    cols = _col_names(cx, "session_message")
    if not {"id", "session_id", "data"}.issubset(cols):
        return [], 0
    has_seq = "seq" in cols
    order = "time_created, id" if "time_created" in cols else "id"
    sel_time = "time_created" if "time_created" in cols else "NULL"
    sel_seq = ", seq" if has_seq else ""
    try:
        rows = cx.execute(
            f"SELECT id, session_id, data, {sel_time} AS tc{sel_seq} "
            f"FROM session_message ORDER BY {order}").fetchall()
    except sqlite3.Error:
        return [], 0
    total = len(rows)
    spends: list[dict] = []
    for r in rows[skip:]:
        r = dict(r)
        sid = str(r["session_id"])
        if has_seq and sid in cutoffs:
            try:
                if int(r["seq"]) <= cutoffs[sid]:
                    continue  # fork-copied history; parent already counted it
            except (TypeError, ValueError):
                pass
        data = r["data"]
        try:
            msg = json.loads(data) if isinstance(data, str) else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(msg, dict):
            continue
        tokens = msg.get("tokens")
        if not isinstance(tokens, dict):
            continue
        in_tok = _tokens(tokens.get("input"))
        out_tok = _tokens(tokens.get("output"))
        if not in_tok and not out_tok:
            continue
        model = msg.get("modelID") or "unknown"
        provider = msg.get("providerID") or "unknown"
        cost = msg.get("cost")
        try:
            cost_f = float(cost) if cost is not None else 0.0
        except (TypeError, ValueError):
            cost_f = 0.0
        spends.append({"session_id": sid, "provider": str(provider),
                       "model": str(model), "input_tokens": in_tok,
                       "output_tokens": out_tok, "cost_usd": cost_f,
                       "ts": _ms_to_iso(r["tc"])})
    return spends, total


def parse_transcript(project: str, path: Path, skip_lines: int = 0):
    """Parse one opencode database.

    ``skip_lines`` counts already-consumed messages in
    ``(time_created, id)`` order (the file-offset mechanism reused for
    SQLite). Returns (session, events, spends, total_messages) where
    session is a representative Session for the database; individual
    sessions are recorded by the caller via ``iter_sessions``.
    """
    # The per-database entry point below is preferred; this keeps the
    # HarnessParser protocol uniform for the CLI ingest loop.
    sessions, _events, spends, total = parse_database(path, skip_messages=skip_lines)
    rep = sessions[0] if sessions else Session(
        id=f"opencode:{path.name}", harness=HARNESS, project=project)
    return rep, [], spends, total


def parse_database(path: Path, skip_messages: int = 0):
    """Full parse: (sessions, events, spends, total_messages)."""
    cx = _ro(path)
    if cx is None:
        return [], [], [], 0
    try:
        tables = _tables(cx)
        sessions = [Session(id=s["id"], harness=HARNESS, project=s["project"],
                            started_at=s["started_at"])
                    for s in _session_rows(cx, tables)]
        cutoffs = _fork_cutoffs(cx, tables)
        spend_dicts, total = _message_spends(cx, tables, cutoffs, skip_messages)
        spends = [Spend(session_id=d["session_id"], provider=d["provider"],
                        model=d["model"], input_tokens=d["input_tokens"],
                        output_tokens=d["output_tokens"], cost_usd=d["cost_usd"])
                  for d in spend_dicts]
        return sessions, [], spends, total
    finally:
        cx.close()


def iter_sessions(path: Path) -> Iterator[Session]:
    """Yield every session in the database (for callers that record them)."""
    sessions, _, _, _ = parse_database(path)
    yield from sessions
