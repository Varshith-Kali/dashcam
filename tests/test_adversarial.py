"""Adversarial tests: the parser and store must survive hostile input.

A coding agent's transcript is untrusted input — a compromised or buggy
harness can write anything. These tests throw garbage at the parser and
demand: never crash, never corrupt the store, chain always verifies.
"""
import json
import random
import string
import subprocess
import sys
import time

from dashcam.ingest import claude_code
from dashcam.models import Session, ToolEvent
from dashcam.store import Store


def _parse_lines(tmp_path, lines, name="fuzz.jsonl"):
    p = tmp_path / name
    # write raw bytes to allow invalid utf-8
    data = b"\n".join(
        l if isinstance(l, bytes) else str(l).encode("utf-8", errors="replace")
        for l in lines
    )
    p.write_bytes(data)
    return claude_code.parse_transcript("proj", p)


def test_garbage_lines_never_crash(tmp_path):
    rng = random.Random(42)
    alphabet = (string.printable.replace("\n", "").replace("\r", "")
                .replace("\x0b", "").replace("\x0c", "")
                + "héllo→世界🔥\x00\x01\x02")
    lines = []
    for _ in range(500):
        kind = rng.random()
        if kind < 0.3:
            lines.append(''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 200))))
        elif kind < 0.5:
            lines.append('{"type": "assistant", "message": {"content": [')
        elif kind < 0.7:
            lines.append(json.dumps({"type": "assistant", "message": None,
                                     "usage": "not-a-dict", "model": 123}))
        elif kind < 0.85:
            lines.append(json.dumps({"type": "assistant", "timestamp": "x",
                                     "message": {"content": [{"type": "tool_use"}]}}))
        else:
            lines.append(b'\xff\xfe\x00bad\x01bytes')
    _session, events, spends, nlines = _parse_lines(tmp_path, lines)
    assert nlines == 500
    assert isinstance(events, list) and isinstance(spends, list)
    # store must accept whatever came out and still verify
    s = Store(tmp_path / "t.db")
    s.record_session(Session(id=_session.id, harness="x", project="y"))
    for e in events:
        s.record_event(e)
    for x in spends:
        s.record_spend(x)
    ok, _ = s.verify()
    assert ok
    s.close()


def test_huge_line_does_not_hang_or_oom(tmp_path):
    big = "x" * (5 * 1024 * 1024)
    lines = [json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
                         "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                  "input": {"command": big}}],
                                     "usage": {"input_tokens": 1, "output_tokens": 1}}})]
    t0 = time.time()
    _session, events, _spends, _ = _parse_lines(tmp_path, lines)
    assert time.time() - t0 < 10
    assert len(events) == 1
    # summary is truncated — the 5MB blob must not land in the DB verbatim
    assert len(events[0].summary) < 1000


def test_deeply_nested_and_wrong_types(tmp_path):
    nested = {"a": {}}
    d = nested
    for _ in range(200):
        d["a"] = {}
        d = d["a"]
    lines = [
        json.dumps({"type": "assistant", "message": {"content": nested}}),
        json.dumps({"type": "assistant", "message": {"content": "just a string"}}),
        json.dumps({"type": "assistant", "message": {"content": [None, 42, []]}}),
        json.dumps([1, 2, 3]),
        json.dumps("a string envelope"),
        json.dumps(None),
    ]
    _session, _events, _spends, nlines = _parse_lines(tmp_path, lines)
    assert nlines == 6


def test_multiprocess_concurrent_ingest_no_fork(tmp_path):
    """Two processes appending simultaneously must not fork the chain."""
    db = str(tmp_path / "t.db")
    Store(db).close()  # create
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from dashcam.store import Store;"
        "from dashcam.models import ToolEvent;"
        f"s = Store({db!r});"
        "n = int(sys.argv[1]);"
        "[s.record_event(ToolEvent(session_id='p%d' % n, ts='2026-01-01T00:00:00Z',"
        " tool='Bash', summary='x')) for _ in range(30)];"
        "s.close()"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(i)],
                              cwd="/home/hatch/workspace/dashcam")
             for i in range(3)]
    for p in procs:
        assert p.wait(timeout=120) == 0, "worker failed"
    s = Store(db)
    n = s.cx.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert n == 90
    ok, msg = s.verify()
    assert ok, msg
    s.close()


def test_crash_recovery_in_subprocess(tmp_path):
    """A hard-killed ingest leaves a consistent store that resumes cleanly."""
    db = tmp_path / "t.db"
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import os;"
        "from dashcam.store import Store;"
        "from dashcam.models import Session, ToolEvent;"
        f"s = Store({str(db)!r});"
        "s.record_session(Session(id='s1', harness='h', project='p'));"
        "s.set_file_offset('/tmp/f.jsonl', 10);"
        "w = s.atomic(); w.__enter__();"
        "s.record_event(ToolEvent(session_id='s1', ts='t', tool='Bash', summary='$ x'));"
        "os._exit(137);"
    )
    p = subprocess.run([sys.executable, "-c", code], cwd="/home/hatch/workspace/dashcam",
                       check=False)
    assert p.returncode == 137  # died mid-transaction
    s = Store(str(db))
    n = s.cx.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert n == 0, "partial rows survived the crash"
    assert s.file_offset("/tmp/f.jsonl") == 10, "offset must not advance on crash"
    ok, _ = s.verify()
    assert ok
    # resume works after the crash
    s.record_event(ToolEvent(session_id="s1", ts="t", tool="Bash", summary="$ y"))
    ok, _ = s.verify()
    assert ok
    s.close()


def test_ingest_is_read_only(tmp_path):
    """Parsing must never modify the harness log (mtime, size, bytes)."""
    lines = [
        {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "model": "m",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "rm -rf /"}}],
                     "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines))
    before = (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
    claude_code.parse_transcript("proj", p)
    after = (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
    assert before == after


def test_no_raw_secrets_in_db(tmp_path):
    """End-to-end: secrets in transcripts must not persist raw in SQLite."""
    secrets = ["ghp_" + "z" * 36, "AKIA" + "Z" * 16, "--password=s3cr3t!"]
    lines = [
        {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "model": "m",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": f"deploy {sec}"}} for sec in secrets],
                     "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines))
    _session, events, _, _ = claude_code.parse_transcript("proj", p)
    s = Store(tmp_path / "t.db")
    for e in events:
        s.record_event(e)
    s.close()
    raw = (tmp_path / "t.db").read_bytes()
    for sec in secrets:
        assert sec.encode() not in raw, f"raw secret leaked: {sec[:8]}..."
