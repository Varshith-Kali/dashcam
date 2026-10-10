"""brief tests: BYOK stays off without a key; only aggregates are sent."""

from typer.testing import CliRunner

from dashcam import brief as brief_mod
from dashcam.cli import app
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store

runner = CliRunner()


def _stats():
    from collections import Counter
    return {"day": "2026-10-08", "sessions": 2,
            "by_harness": {"claude-code": 2}, "events": 30,
            "tools": Counter({"Bash": 20, "Read": 10}), "files": {},
            "by_model": {"anthropic/m": {"in": 1000, "out": 100, "cost": 1.5}},
            "total_in": 1000, "total_out": 100, "total_cost": 1.5,
            "flags": []}


def test_no_config_means_off(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHCAM_API_KEY", raising=False)
    monkeypatch.setattr(brief_mod, "config_path", lambda: tmp_path / "nope.json")
    assert brief_mod.load_config() is None


def test_env_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHCAM_API_KEY", "sk-test")
    monkeypatch.setenv("DASHCAM_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(brief_mod, "config_path", lambda: tmp_path / "nope.json")
    cfg = brief_mod.load_config()
    assert cfg.provider == "anthropic" and cfg.api_key == "sk-test"


def test_bad_provider_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHCAM_API_KEY", "sk-test")
    monkeypatch.setenv("DASHCAM_LLM_PROVIDER", "watson")
    monkeypatch.setattr(brief_mod, "config_path", lambda: tmp_path / "nope.json")
    try:
        brief_mod.load_config()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_brief_text_is_aggregates_only():
    t = brief_mod.build_brief_text(_stats())
    assert "Sessions: 2" in t and "$1.50" in t and "Bash×20" in t
    # raw summaries / prompt text must never appear
    assert "pytest" not in t and "sk-" not in t


def test_narrate_openai_shape(monkeypatch):
    seen = {}

    def fake_post(url, headers, payload, timeout=60):
        seen.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": "  hello briefing  "}}]}

    monkeypatch.setattr(brief_mod, "_post", fake_post)
    cfg = brief_mod.LLMConfig(provider="openai", api_key="sk-x",
                              model="gpt-5.6-luna", base_url=None)
    out = brief_mod.narrate(cfg, "brief text")
    assert out == "hello briefing"
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-x"
    msgs = seen["payload"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[1]["content"] == "brief text"


def test_narrate_anthropic_shape(monkeypatch):
    seen = {}

    def fake_post(url, headers, payload, timeout=60):
        seen.update(url=url, headers=headers, payload=payload)
        return {"content": [{"type": "text", "text": "briefing"}]}

    monkeypatch.setattr(brief_mod, "_post", fake_post)
    cfg = brief_mod.LLMConfig(provider="anthropic", api_key="sk-y",
                              model="claude-haiku-4-5", base_url=None)
    out = brief_mod.narrate(cfg, "brief text")
    assert out == "briefing"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-y"
    assert seen["payload"]["system"] == brief_mod.SYSTEM_PROMPT


def test_cli_brief_without_key_prints_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHCAM_API_KEY", raising=False)
    monkeypatch.setattr(brief_mod, "config_path", lambda: tmp_path / "nope.json")
    r = runner.invoke(app, ["brief", "--db", str(tmp_path / "t.db")])
    assert r.exit_code == 1
    assert "BYOK" in r.output


def test_cli_brief_narrates(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    db = tmp_path / "t.db"
    s = Store(db)
    ts = datetime.now(timezone.utc).isoformat()
    s.record_session(Session(id="s1", harness="claude-code", project="p",
                             started_at=ts))
    s.record_event(ToolEvent(session_id="s1", ts=ts, tool="Bash",
                             summary="$ make test"))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                         input_tokens=100, output_tokens=10, cost_usd=0.1))
    s.close()
    monkeypatch.setenv("DASHCAM_API_KEY", "sk-test")
    monkeypatch.setattr(brief_mod, "narrate",
                        lambda cfg, text: f"NARRATED[{len(text)} chars]")
    r = runner.invoke(app, ["brief", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "NARRATED" in r.output
