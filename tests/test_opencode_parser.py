"""opencode parser tests — fixtures build a real SQLite DB with the
verified schema (session/session_v2/session_message tables and the
message-data JSON shapes documented in ccusage's opencode adapter).
"""
import json
import sqlite3

from dashcam.ingest import opencode


def _db(path, with_v2=True):
    cx = sqlite3.connect(path)
    cx.execute("CREATE TABLE session(id TEXT PRIMARY KEY, time_created INTEGER, "
               "cost REAL, tokens_input INTEGER, tokens_output INTEGER, "
               "tokens_cache_read INTEGER, tokens_cache_write INTEGER, "
               "directory TEXT)")
    if with_v2:
        cx.execute("CREATE TABLE session_v2(id TEXT PRIMARY KEY, time_created INTEGER, "
                   "cost REAL, tokens_input INTEGER, tokens_output INTEGER, "
                   "tokens_cache_read INTEGER, tokens_cache_write INTEGER, "
                   "fork_session_id TEXT, fork_boundary INTEGER)")
    cx.execute("CREATE TABLE session_message(id TEXT PRIMARY KEY, session_id TEXT, "
               "type TEXT, data TEXT, time_created INTEGER, seq INTEGER)")
    return cx


def _msg(mid, sid, tokens, model="anthropic/claude-sonnet-5-5",
         provider="anthropic", cost=0.0, tc=1760000000000, seq=1):
    return (mid, sid, "assistant",
            json.dumps({"tokens": tokens, "modelID": model,
                        "providerID": provider, "cost": cost,
                        "time": {"created": tc}}),
            tc, seq)


def test_opencode_parses_sessions_and_spend(tmp_path):
    db = tmp_path / "opencode.db"
    cx = _db(db, with_v2=False)
    cx.execute("INSERT INTO session VALUES('s1', 1760000000000, 0.5, 1000, 200, 0, 0, '/home/u/proj')")
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("m1", "s1", {"input": 1000, "output": 200}, cost=0.5))
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("m2", "s1", {"input": 500, "output": 100}, cost=0.2,
                    tc=1760000001000, seq=2))
    cx.commit()
    cx.close()

    sessions, _events, spends, total = opencode.parse_database(db)
    assert len(sessions) == 1
    assert sessions[0].id == "s1"
    assert sessions[0].harness == "opencode"
    assert sessions[0].project == "/home/u/proj"
    assert len(spends) == 2
    assert spends[0].provider == "anthropic"
    assert spends[0].model == "anthropic/claude-sonnet-5-5"
    assert spends[0].input_tokens == 1000
    assert spends[0].cost_usd == 0.5  # harness's own pre-calculated cost kept
    assert total == 2


def test_opencode_skips_fork_copied_rows(tmp_path):
    db = tmp_path / "opencode.db"
    cx = _db(db)
    cx.execute("INSERT INTO session_v2 VALUES('parent', 1760000000000, 1.0, 2000, 400, 0, 0, NULL, NULL)")
    cx.execute("INSERT INTO session_v2 VALUES('fork', 1760000005000, 0.1, 200, 40, 0, 0, 'parent', 2)")
    for i, tc in enumerate((1760000000000, 1760000001000, 1760000002000), start=1):
        cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
                   _msg(f"p{i}", "parent", {"input": 100, "output": 20},
                        tc=tc, seq=i))
    # fork copies parent's first 2 messages (new ids, same seqs) + 1 own
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("f1", "fork", {"input": 100, "output": 20},
                    tc=1760000005000, seq=1))
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("f2", "fork", {"input": 100, "output": 20},
                    tc=1760000006000, seq=2))
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("f3", "fork", {"input": 200, "output": 40},
                    tc=1760000007000, seq=3))
    cx.commit()
    cx.close()

    sessions, _events, spends, _total = opencode.parse_database(db)
    assert {s.id for s in sessions} == {"parent", "fork"}
    # 3 parent + 1 fork-own (2 copied rows skipped)
    assert len(spends) == 4
    fork_spends = [s for s in spends if s.session_id == "fork"]
    assert len(fork_spends) == 1
    assert fork_spends[0].input_tokens == 200


def test_opencode_incremental_offset(tmp_path):
    db = tmp_path / "opencode.db"
    cx = _db(db, with_v2=False)
    cx.execute("INSERT INTO session VALUES('s1', 1760000000000, 0, 0, 0, 0, 0, '/p')")
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("m1", "s1", {"input": 10, "output": 5}))
    cx.commit()
    cx.close()
    _, _, spends1, total1 = opencode.parse_database(db, skip_messages=0)
    assert len(spends1) == 1 and total1 == 1
    # new message arrives; resume from offset -> only the new one
    cx = sqlite3.connect(db)
    cx.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               _msg("m2", "s1", {"input": 20, "output": 5}, tc=1760000009000, seq=2))
    cx.commit()
    cx.close()
    _, _, spends2, total2 = opencode.parse_database(db, skip_messages=total1)
    assert len(spends2) == 1
    assert spends2[0].input_tokens == 20
    assert total2 == 2


def test_opencode_handles_garbage_and_missing_tables(tmp_path):
    db = tmp_path / "opencode.db"
    cx = sqlite3.connect(db)
    cx.execute("CREATE TABLE session_message(id TEXT, session_id TEXT, data TEXT)")
    cx.execute("INSERT INTO session_message VALUES('x', 's9', 'not json')")
    cx.execute("INSERT INTO session_message VALUES('y', 's9', '{\"tokens\": {}}')")
    cx.commit()
    cx.close()
    sessions, _events, spends, total = opencode.parse_database(db)
    assert sessions == []  # no session table -> no sessions, but no crash
    assert spends == []
    assert total == 2


def test_opencode_iter_transcripts(tmp_path):
    d = tmp_path / "share" / "opencode"
    d.mkdir(parents=True)
    (d / "opencode.db").write_bytes(b"")
    (d / "other.txt").write_text("x")
    found = list(opencode.iter_transcripts(tmp_path / "share" / "opencode"))
    assert len(found) == 1 and found[0][1].name == "opencode.db"
