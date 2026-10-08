"""Gemini CLI parser tests — fixtures mirror the documented chat format.

Reference: ccusage ``rust/adapters/gemini/src/parser.rs`` (``GeminiRecord``
field names: ``sessionId``/``session_id``, ``model``, ``timestamp``/
``createdAt``/``startTime``/``lastUpdated``, ``stats``/``tokens``/
``result.stats`` with input/output/thoughts/tool key aliases; ``messages``
with ``parts[].functionCall`` in the Gemini API shape).
"""
import json

from dashcam.ingest import gemini


def _chat_file(tmp_path, name, obj):
    d = tmp_path / "tmp" / "proj-hash" / "chats"
    d.mkdir(parents=True)
    p = d / name
    p.write_text(json.dumps(obj))
    return p


def test_gemini_parses_json_array_with_stats_and_function_calls(tmp_path):
    p = _chat_file(tmp_path, "c1.json", [
        {"sessionId": "gem-sess-1", "model": "gemini-2.5-pro",
         "startTime": "2026-10-08T09:00:00Z",
         "stats": {"input_tokens": 3000, "output_tokens": 500,
                   "cached_tokens": 1000},
         "messages": [
             {"role": "model", "parts": [
                 {"functionCall": {"name": "run_shell_command",
                                  "args": {"command": "ls -la"}}}]},
             {"role": "user", "parts": [{"text": "hi"}]},
         ]},
        {"sessionId": "gem-sess-1", "model": "gemini-2.5-pro",
         "lastUpdated": "2026-10-08T09:05:00Z",
         "result": {"stats": {"input": 1000, "output": 200}},
         "messages": []},
    ])
    session, events, spends, _nlines = gemini.parse_transcript("gemini", p)
    assert session.id == "gem-sess-1"
    assert session.harness == "gemini"
    assert len(spends) == 2
    assert spends[0].provider == "google"
    assert spends[0].model == "gemini-2.5-pro"
    assert spends[0].input_tokens == 3000
    assert spends[0].output_tokens == 500
    assert spends[1].input_tokens == 1000  # "input" alias via result.stats
    assert len(events) == 1
    assert events[0].tool == "run_shell_command"
    assert "ls -la" in events[0].summary


def test_gemini_parses_jsonl_and_skips_garbage(tmp_path):
    d = tmp_path / "tmp" / "h" / "chats"
    d.mkdir(parents=True)
    p = d / "c2.jsonl"
    p.write_text(
        json.dumps({"session_id": "g2", "model": "gemini-2.0-flash",
                    "timestamp": "2026-10-08T10:00:00Z",
                    "tokens": {"promptTokenCount": 700, "candidatesTokenCount": 90}}) + "\n"
        + "garbage line\n"
        + json.dumps({"type": "unrelated"}) + "\n")
    session, events, spends, _nlines = gemini.parse_transcript("gemini", p)
    assert session.id == "g2"
    assert len(spends) == 1
    assert spends[0].input_tokens == 700
    assert spends[0].output_tokens == 90
    assert events == []


def test_gemini_iter_transcripts_finds_json_and_jsonl(tmp_path):
    p1 = _chat_file(tmp_path, "a.json", [])
    d = tmp_path / "tmp" / "h2" / "chats"
    d.mkdir(parents=True)
    p2 = d / "b.jsonl"
    p2.write_text("{}\n")
    found = sorted(f for _, f in gemini.iter_transcripts(tmp_path))
    assert found == sorted([p1, p2])


def test_gemini_handles_missing_everything(tmp_path):
    p = _chat_file(tmp_path, "empty.json", [{"foo": "bar"}, None, 42])
    session, events, spends, _nlines = gemini.parse_transcript("gemini", p)
    assert events == [] and spends == []
    assert session.id == "empty"
