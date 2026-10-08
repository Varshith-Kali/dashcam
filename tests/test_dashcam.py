"""Tests for dashcam — the receipt layer must be bulletproof."""
import json
import os
import stat
import threading

import pytest

from dashcam import cost as cost_mod
from dashcam import demo as demo_mod
from dashcam import redact as redact_mod
from dashcam.ingest import claude_code
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store


def _db(tmp_path, name="t.db"):
    return Store(tmp_path / name)


# -- chain -----------------------------------------------------------------
def test_chain_interleaved_order_verifies(tmp_path):
    """Events and spend interleaved (as real multi-file ingest does) must verify."""
    s = _db(tmp_path)
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Bash", summary="$ ls"))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                         input_tokens=1, output_tokens=1, cost_usd=0.0))
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:01:00Z",
                             tool="Read", summary="Read a.py"))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                         input_tokens=2, output_tokens=2, cost_usd=0.0))
    ok, msg = s.verify()
    assert ok, msg
    s.close()


def test_chain_tamper_middle_row_detected(tmp_path):
    s = _db(tmp_path)
    for i in range(3):
        s.record_event(ToolEvent(session_id="s1", ts=f"2026-01-01T00:0{i}:00Z",
                                 tool="Bash", summary=f"$ cmd{i}"))
    s.cx.execute("UPDATE events SET summary='pwned' WHERE seq=2")
    ok, msg = s.verify()
    assert not ok and "seq=2" in msg
    s.close()


def test_chain_deletion_detected(tmp_path):
    s = _db(tmp_path)
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Read", summary="Read foo.py"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:01:00Z",
                             tool="Bash", summary="$ pytest"))
    s.cx.execute("DELETE FROM events WHERE seq=1")
    ok, _msg = s.verify()
    assert not ok
    s.close()


def test_chain_head_tamper_detected(tmp_path):
    s = _db(tmp_path)
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Bash", summary="$ ls"))
    s.cx.execute("UPDATE meta SET value=? WHERE key='chain_head'", ("0" * 64,))
    ok, msg = s.verify()
    assert not ok and "head mismatch" in msg
    s.close()


def test_atomic_rollback_on_error(tmp_path):
    """A failure inside atomic() leaves no partial rows."""
    s = _db(tmp_path)
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Bash", summary="$ ok"))
    with pytest.raises(RuntimeError), s.atomic():
        s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:01:00Z",
                                 tool="Bash", summary="$ doomed"))
        s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                             input_tokens=1, output_tokens=1, cost_usd=0.0))
        raise RuntimeError("boom")
    n = s.cx.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert n == 1
    ok, _ = s.verify()
    assert ok
    s.close()


def test_concurrent_writes_stay_consistent(tmp_path):
    s = _db(tmp_path)
    def worker(n):
        for i in range(20):
            s.record_event(ToolEvent(session_id=f"s{n}", ts="2026-01-01T00:00:00Z",
                                     tool="Bash", summary=f"$ t{n}-{i}"))
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    n = s.cx.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert n == 80
    ok, msg = s.verify()
    assert ok, msg
    s.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_restrictive_permissions(tmp_path):
    s = _db(tmp_path)
    s.close()
    db_mode = stat.S_IMODE(os.stat(s.path).st_mode)
    dir_mode = stat.S_IMODE(os.stat(s.path.parent).st_mode)
    assert db_mode == 0o600, oct(db_mode)
    assert dir_mode == 0o700, oct(dir_mode)


# -- redaction ---------------------------------------------------------------
@pytest.mark.parametrize("secret", [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a" * 36,
    "github_pat_" + "b" * 82,
    "sk-" + "c" * 40,
    "xoxb-1234-5678-abcdef",
    "--password=hunter2",
    "api_key: deadbeef123",
    "Bearer eyJhbGciOiJIUzI1NiJ9",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_redact_patterns(secret):
    out = redact_mod.redact(f"run with {secret} now")
    assert secret not in out
    assert "[REDACTED]" in out


def test_redact_leaves_clean_text_alone():
    assert redact_mod.redact("$ pytest tests/ -q") == "$ pytest tests/ -q"
    assert redact_mod.redact("") == ""
    assert redact_mod.redact(None) == ""


def test_parser_redacts_secrets_before_persist(tmp_path):
    lines = [
        {"type": "assistant", "timestamp": "2026-10-01T10:00:05.000Z", "model": "claude-sonnet-4-6",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                  "input": {"command": "curl -H 'Authorization: Bearer secret123abc' https://api.example.com"}}],
                     "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines))
    _session, events, _, _ = claude_code.parse_transcript("proj", p)
    assert "secret123abc" not in events[0].summary
    assert "[REDACTED]" in events[0].summary


# -- parser ------------------------------------------------------------------
def test_claude_code_parser(tmp_path):
    lines = [
        {"type": "user", "timestamp": "2026-10-01T10:00:00.000Z",
         "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "timestamp": "2026-10-01T10:00:05.000Z", "model": "claude-sonnet-4-6",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                  "input": {"command": "ls -la"}}],
                     "usage": {"input_tokens": 1000, "output_tokens": 200}}},
        {"type": "assistant", "timestamp": "2026-10-01T10:00:09.000Z", "model": "claude-sonnet-4-6",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 500, "output_tokens": 50}}},
        "not json at all {{{",
    ]
    p = tmp_path / "sess123.jsonl"
    p.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines))
    session, events, spends, nlines = claude_code.parse_transcript("myproj", p)
    assert session.id == "sess123"
    assert session.harness == "claude-code"
    assert len(events) == 1
    assert events[0].tool == "Bash"
    assert "ls -la" in events[0].summary
    assert len(spends) == 2
    assert spends[0].provider == "anthropic"
    assert spends[0].input_tokens == 1000
    assert nlines == 4


def test_resume_produces_no_duplicates(tmp_path):
    """Simulate two ingest passes over a growing file: no duplicate rows."""
    lines = [
        {"type": "assistant", "timestamp": "2026-10-01T10:00:05.000Z", "model": "claude-sonnet-4-6",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                  "input": {"command": "ls"}}],
                     "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines))
    s = _db(tmp_path)

    def ingest_once():
        offset = s.file_offset(str(p))
        session, events, spends, nlines = claude_code.parse_transcript("proj", p,
                                                                      skip_lines=offset)
        with s.atomic():
            if not s.has_session(session.id):
                s.record_session(session)
            for e in events:
                s.record_event(e)
            for x in spends:
                s.record_spend(x)
            s.set_file_offset(str(p), nlines)

    ingest_once()
    # file grows by one line
    lines.append(
        {"type": "assistant", "timestamp": "2026-10-01T10:01:05.000Z", "model": "claude-sonnet-4-6",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "id": "b", "name": "Read",
                                  "input": {"file_path": "a.py"}}],
                     "usage": {"input_tokens": 10, "output_tokens": 5}}})
    p.write_text("\n".join(json.dumps(l) for l in lines))
    ingest_once()
    ingest_once()  # third pass: nothing new

    n_ev = s.cx.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    n_sp = s.cx.execute("SELECT COUNT(*) c FROM spend").fetchone()["c"]
    assert (n_ev, n_sp) == (2, 2)
    ok, _ = s.verify()
    assert ok
    s.close()


# -- cost --------------------------------------------------------------------
def test_cost_math():
    pricing = {"anthropic/claude-sonnet-4-6": {"input": 3.0, "output": 15.0}}
    usd, known = cost_mod.cost_usd("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, pricing)
    assert known and usd == 18.0
    usd, known = cost_mod.cost_usd("anthropic", "claude-nonexistent-9", 100, 100, pricing)
    assert not known and usd == 0.0


# -- demo --------------------------------------------------------------------
def test_demo_uses_scratch_db_and_cleans(tmp_path):
    from dashcam.store import demo_db_path  # noqa: F401  (documents the real path)
    scratch = tmp_path / "demo.db"
    store = Store(scratch)
    demo_mod.run_demo(store)
    store.close()
    assert scratch.exists()
    assert demo_mod.clean_demo(scratch)
    assert not scratch.exists()
    assert not demo_mod.clean_demo(scratch)  # idempotent


def test_export_json(tmp_path):
    import io
    s = _db(tmp_path)
    s.record_session(Session(id="s1", harness="claude-code", project="p"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Bash", summary="$ ls"))
    payload = {
        "sessions": [dict(r) for r in s.cx.execute("SELECT * FROM sessions")],
        "events": [dict(r) for r in s.cx.execute("SELECT * FROM events ORDER BY seq")],
        "spend": [dict(r) for r in s.cx.execute("SELECT * FROM spend ORDER BY seq")],
    }
    buf = io.StringIO()
    json.dump(payload, buf)
    data = json.loads(buf.getvalue())
    assert data["sessions"][0]["id"] == "s1"
    assert data["events"][0]["tool"] == "Bash"
    s.close()
