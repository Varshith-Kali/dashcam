"""Codex CLI parser tests — fixtures mirror the documented rollout format.

Reference: ccusage ``rust/adapters/codex/src/README.md`` (sessions layout,
``event_msg`` / ``token_count`` / ``last_token_usage`` contract) and the
Codex protocol crate for ``response_item`` / ``function_call`` shapes.
"""
import json

from dashcam.ingest import codex


def _write(path, lines):
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return path


def _rollout(tmp_path):
    d = tmp_path / "sessions" / "2026" / "10" / "08"
    d.mkdir(parents=True)
    p = d / "rollout-2026-10-08T09-00-00-abc123.jsonl"
    _write(p, [
        {"timestamp": "2026-10-08T09:00:00Z", "type": "session_meta",
         "payload": {"id": "sess-codex-1", "cwd": "/home/u/proj",
                     "model": "gpt-5.2", "cli_version": "0.200.0"}},
        {"timestamp": "2026-10-08T09:01:00Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": "{\"command\":\"pytest -q\"}"}},
        {"timestamp": "2026-10-08T09:02:00Z", "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"last_token_usage": {"input_tokens": 5000,
                                                   "output_tokens": 800},
                              "total_token_usage": {"input_tokens": 5000,
                                                    "output_tokens": 800}}}},
        {"timestamp": "2026-10-08T09:03:00Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "done"}]}},
        "not json at all",
        {"timestamp": "2026-10-08T09:04:00Z", "type": "event_msg",
         "payload": {"type": "thread_settings_applied",
                     "thread_settings": {"service_tier": "default"}}},
    ])
    return p


def test_codex_parses_session_tokens_and_tool_calls(tmp_path):
    p = _rollout(tmp_path)
    session, events, spends, nlines = codex.parse_transcript("codex", p)
    assert session.id == "sess-codex-1"
    assert session.harness == "codex"
    assert session.project == "/home/u/proj"
    assert len(events) == 1
    assert events[0].tool == "shell"
    assert "pytest -q" in events[0].summary
    assert len(spends) == 1
    assert spends[0].provider == "openai"
    assert spends[0].model == "gpt-5.2"
    assert spends[0].input_tokens == 5000
    assert spends[0].output_tokens == 800
    assert nlines == 6


def test_codex_skips_unknown_shapes_and_garbage(tmp_path):
    p = tmp_path / "r.jsonl"
    _write(p, [
        {"type": "weird_future_record", "payload": {"x": 1}},
        {"type": "event_msg", "payload": {"type": "token_count"}},  # no info
        {"type": "response_item", "payload": {"type": "function_call"}},  # no name
        [1, 2, 3],
        "just a string",
    ])
    session, events, spends, nlines = codex.parse_transcript("codex", p)
    assert events == [] and spends == []
    assert session.id == "r"  # filename stem fallback
    assert nlines == 5


def test_codex_iter_transcripts_finds_nested(tmp_path):
    p = _rollout(tmp_path)
    found = list(codex.iter_transcripts(tmp_path))
    assert len(found) == 1
    assert found[0][1] == p


def test_codex_archived_dir_also_scanned(tmp_path):
    d = tmp_path / "archived_sessions"
    d.mkdir()
    p = d / "old.jsonl"
    _write(p, [{"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                "payload": {"id": "old-1"}}])
    found = [f for _, f in codex.iter_transcripts(tmp_path)]
    assert p in found
