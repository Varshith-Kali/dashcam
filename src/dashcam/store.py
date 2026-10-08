"""Tamper-evident local receipt store.

Every recorded row is hash-chained in global insertion order::

    hash = sha256(prev_hash + canonical_json(kind + payload))

Tampering with any row — or deleting one — breaks the chain, which
``dashcam verify`` detects. SQLite with WAL mode; everything stays in
``~/.dashcam/receipts.db``. No network, no accounts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .models import Session, Spend, ToolEvent

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, harness TEXT NOT NULL, project TEXT NOT NULL,
  started_at TEXT, ended_at TEXT, source TEXT NOT NULL DEFAULT 'ingest');
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, seq INTEGER NOT NULL,
  session_id TEXT NOT NULL, ts TEXT, tool TEXT NOT NULL,
  summary TEXT NOT NULL, detail TEXT DEFAULT '',
  source TEXT NOT NULL DEFAULT 'ingest', prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spend(
  id INTEGER PRIMARY KEY AUTOINCREMENT, seq INTEGER NOT NULL,
  session_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'ingest',
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_spend_session ON spend(session_id);
CREATE INDEX IF NOT EXISTS idx_events_seq ON events(seq);
CREATE INDEX IF NOT EXISTS idx_spend_seq ON spend(seq);
"""


def default_db_path() -> Path:
    return Path.home() / ".dashcam" / "receipts.db"


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _chain_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(str(self.path))
        self.cx.row_factory = sqlite3.Row
        self.cx.executescript(SCHEMA)
        for key, default in (("chain_head", GENESIS), ("chain_seq", "0")):
            row = self.cx.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if row is None:
                self.cx.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (key, default))
        self.cx.commit()

    # -- chain primitives -------------------------------------------------
    def _head(self) -> str:
        return self.cx.execute("SELECT value FROM meta WHERE key='chain_head'").fetchone()[0]

    def _seal(self, kind: str, payload: dict) -> tuple[str, str, int]:
        """Advance the chain; return (prev_hash, hash, seq)."""
        body = {"kind": kind, **payload}
        prev = self._head()
        seq = int(self.cx.execute("SELECT value FROM meta WHERE key='chain_seq'").fetchone()[0]) + 1
        h = _chain_hash(prev, _canonical(body))
        self.cx.execute("UPDATE meta SET value=? WHERE key='chain_head'", (h,))
        self.cx.execute("UPDATE meta SET value=? WHERE key='chain_seq'", (str(seq),))
        return prev, h, seq

    # -- writers ----------------------------------------------------------
    def record_session(self, s: Session) -> None:
        self.cx.execute(
            "INSERT OR IGNORE INTO sessions(id, harness, project, started_at, ended_at, source)"
            " VALUES (?,?,?,?,?,?)",
            (s.id, s.harness, s.project, s.started_at, s.ended_at, s.source),
        )
        self.cx.commit()

    def has_session(self, session_id: str) -> bool:
        return (
            self.cx.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
            is not None
        )

    def record_event(self, e: ToolEvent) -> int:
        payload = {
            "session_id": e.session_id, "ts": e.ts, "tool": e.tool,
            "summary": e.summary, "detail": e.detail, "source": e.source,
        }
        prev, h, seq = self._seal("event", payload)
        cur = self.cx.execute(
            "INSERT INTO events(seq, session_id, ts, tool, summary, detail, source, prev_hash, hash)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (seq, e.session_id, e.ts, e.tool, e.summary, e.detail, e.source, prev, h),
        )
        self.cx.commit()
        return cur.lastrowid

    def record_spend(self, s: Spend) -> int:
        payload = {
            "session_id": s.session_id, "provider": s.provider, "model": s.model,
            "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
            "cost_usd": s.cost_usd, "source": s.source,
        }
        prev, h, seq = self._seal("spend", payload)
        cur = self.cx.execute(
            "INSERT INTO spend(seq, session_id, provider, model, input_tokens, output_tokens,"
            " cost_usd, source, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (seq, s.session_id, s.provider, s.model, s.input_tokens, s.output_tokens,
             s.cost_usd, s.source, prev, h),
        )
        self.cx.commit()
        return cur.lastrowid

    def file_offset(self, path: str) -> int:
        """How many lines of a transcript file were already ingested (for resume)."""
        row = self.cx.execute("SELECT value FROM meta WHERE key=?", (f"file:{path}",)).fetchone()
        return int(row[0]) if row else 0

    def set_file_offset(self, path: str, lines: int) -> None:
        self.cx.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (f"file:{path}", str(lines), str(lines)),
        )
        self.cx.commit()

    # -- verification ------------------------------------------------------
    def verify(self) -> tuple[bool, str]:
        """Recompute the whole chain in insertion order. Returns (ok, message)."""
        rows = self.cx.execute(
            """
            SELECT seq, 'event' AS kind, session_id, ts, tool, summary, detail, source,
                   NULL AS provider, NULL AS model, NULL AS input_tokens,
                   NULL AS output_tokens, NULL AS cost_usd, prev_hash, hash
            FROM events
            UNION ALL
            SELECT seq, 'spend' AS kind, session_id, NULL, NULL, NULL, NULL, source,
                   provider, model, input_tokens, output_tokens, cost_usd, prev_hash, hash
            FROM spend
            ORDER BY seq
            """
        ).fetchall()
        prev = GENESIS
        for r in rows:
            if r["kind"] == "event":
                payload = {"kind": "event", "session_id": r["session_id"], "ts": r["ts"],
                           "tool": r["tool"], "summary": r["summary"],
                           "detail": r["detail"], "source": r["source"]}
            else:
                payload = {"kind": "spend", "session_id": r["session_id"],
                           "provider": r["provider"], "model": r["model"],
                           "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                           "cost_usd": r["cost_usd"], "source": r["source"]}
            expect = _chain_hash(prev, _canonical(payload))
            if r["prev_hash"] != prev or r["hash"] != expect:
                return False, f"chain broken at seq={r['seq']} ({r['kind']})"
            prev = r["hash"]
        if self._head() != prev:
            return False, "chain head mismatch — rows deleted or head tampered"
        n_ev = self.cx.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        n_sp = self.cx.execute("SELECT COUNT(*) c FROM spend").fetchone()["c"]
        return True, f"chain intact: {n_ev} events, {n_sp} spend rows verified"

    def close(self) -> None:
        self.cx.close()
