import json

from dashcam.ingest import claude_code
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store


def test_chain_intact_and_tamper_detected(tmp_path):
    s = Store(tmp_path / "t.db")
    s.record_session(Session(id="s1", harness="claude-code", project="p"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Bash", summary="$ ls"))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="claude-sonnet-4-6",
                         input_tokens=100, output_tokens=50, cost_usd=0.01))
    ok, msg = s.verify()
    assert ok, msg

    # Tamper with a row: chain must break.
    s.cx.execute("UPDATE events SET summary='pwned' WHERE id=1")
    s.cx.commit()
    ok, msg = s.verify()
    assert not ok
    assert "broken" in msg
    s.close()


def test_chain_head_catches_deletion(tmp_path):
    s = Store(tmp_path / "t.db")
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:00:00Z",
                             tool="Read", summary="Read foo.py"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-01-01T00:01:00Z",
                             tool="Bash", summary="$ pytest"))
    ok, _ = s.verify()
    assert ok
    s.cx.execute("DELETE FROM events WHERE id=1")
    s.cx.commit()
    ok, _msg = s.verify()
    assert not ok
    s.close()


def test_file_offsets_resume(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.file_offset("/x/y.jsonl") == 0
    s.set_file_offset("/x/y.jsonl", 42)
    assert s.file_offset("/x/y.jsonl") == 42
    s.close()


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
    assert nlines == 4  # malformed line counted but skipped


def test_cost_math():
    from dashcam import cost as cost_mod
    pricing = {"anthropic/claude-sonnet-4-6": {"input": 3.0, "output": 15.0}}
    usd, known = cost_mod.cost_usd("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000, pricing)
    assert known and usd == 18.0
    usd, known = cost_mod.cost_usd("anthropic", "claude-nonexistent-9", 100, 100, pricing)
    assert not known and usd == 0.0
