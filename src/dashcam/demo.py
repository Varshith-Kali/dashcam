"""Seeded demo — the first-run experience must never be empty.

``dashcam demo`` ingests a small synthetic Claude Code transcript (bundled as
a fixture, clearly tagged source='demo') and prints the digest, so a new
user sees the product working in under a minute. Demo rows are tagged and
can be wiped with ``dashcam demo --clean``.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from rich.console import Console

from . import cost as cost_mod
from .digest import day_stats, render_digest
from .ingest import claude_code
from .models import Session
from .store import Store

DEMO_LINES = [
    {"type": "user", "timestamp": "2026-10-08T09:14:00.000Z",
     "message": {"role": "user", "content": "add retry logic to the api client"}},
    {"type": "assistant", "timestamp": "2026-10-08T09:14:12.000Z", "model": "claude-sonnet-4-6",
     "message": {"role": "assistant",
                 "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                              "input": {"file_path": "src/api_client.py"}}],
                 "usage": {"input_tokens": 4200, "output_tokens": 180}}},
    {"type": "assistant", "timestamp": "2026-10-08T09:14:40.000Z", "model": "claude-sonnet-4-6",
     "message": {"role": "assistant",
                 "content": [{"type": "tool_use", "id": "t2", "name": "Edit",
                              "input": {"file_path": "src/api_client.py"}}],
                 "usage": {"input_tokens": 5100, "output_tokens": 640}}},
    {"type": "assistant", "timestamp": "2026-10-08T09:15:02.000Z", "model": "claude-sonnet-4-6",
     "message": {"role": "assistant",
                 "content": [{"type": "tool_use", "id": "t3", "name": "Bash",
                              "input": {"command": "pytest tests/test_api_client.py -q"}}],
                 "usage": {"input_tokens": 2800, "output_tokens": 220}}},
    {"type": "assistant", "timestamp": "2026-10-08T09:15:31.000Z", "model": "claude-sonnet-4-6",
     "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "Done — 12 tests pass."}],
                 "usage": {"input_tokens": 1900, "output_tokens": 90}}},
]


def _write_demo_transcript() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="dashcam-demo-")) / "demo-session.jsonl"
    tmp.write_text("\n".join(json.dumps(l) for l in DEMO_LINES), encoding="utf-8")
    return tmp


def run_demo(store: Store, console: Console | None = None) -> None:
    console = console or Console()
    path = _write_demo_transcript()
    session, events, spends, _ = claude_code.parse_transcript("demo-project", path)
    session = Session(id="demo-" + session.id, harness="claude-code", project="demo-project",
                      started_at=session.started_at, ended_at=session.ended_at, source="demo")
    pricing = cost_mod.load_pricing()
    store.record_session(session)
    for e in events:
        e.session_id = session.id
        e.source = "demo"
        store.record_event(e)
    for s in spends:
        s.session_id = session.id
        s.source = "demo"
        s.cost_usd, _ = cost_mod.cost_usd(s.provider, s.model, s.input_tokens, s.output_tokens,
                                          pricing)
        store.record_spend(s)
    console.print("[bold]Demo session recorded.[/bold] Here's your digest:\n")
    render_digest(day_stats(store, day=(session.started_at or "")[:10]), console)
    ok, msg = store.verify()
    console.print(f"\n[{'green' if ok else 'red'}]verify: {msg}[/]")


def clean_demo(store: Store) -> int:
    """Remove demo rows. Returns number of sessions removed."""
    n = store.cx.execute("SELECT COUNT(*) c FROM sessions WHERE source='demo'").fetchone()["c"]
    store.cx.execute("DELETE FROM events WHERE source='demo'")
    store.cx.execute("DELETE FROM spend WHERE source='demo'")
    store.cx.execute("DELETE FROM sessions WHERE source='demo'")
    store.cx.commit()
    return n
