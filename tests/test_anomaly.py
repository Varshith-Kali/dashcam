"""Anomaly detection tests: baselines are median-based and need enough history."""
from datetime import datetime, timedelta, timezone

from dashcam import anomaly as an
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store


def _session(store, sid, project, harness, days_ago, cost, tools):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    store.record_session(Session(id=sid, harness=harness, project=project,
                                 started_at=ts, ended_at=ts))
    for i in range(tools):
        store.record_event(ToolEvent(session_id=sid, ts=ts, tool="Bash",
                                     summary=f"$ cmd{i}"))
    if cost:
        store.record_spend(Spend(session_id=sid, provider="anthropic",
                                 model="m", input_tokens=1000,
                                 output_tokens=100, cost_usd=cost))


def _baseline(store, n=6, **kw):
    for i in range(n):
        _session(store, f"base-{i}", "proj", "claude-code", days_ago=10,
                 cost=kw.get("cost", 2.0), tools=kw.get("tools", 20))


def test_no_flags_without_enough_history(tmp_path):
    s = Store(tmp_path / "t.db")
    _baseline(s, n=3)  # below MIN_SESSIONS
    _session(s, "new", "proj", "claude-code", days_ago=0, cost=50.0, tools=500)
    assert an.flag_unusual(s) == []
    s.close()


def test_flags_cost_outlier(tmp_path):
    s = Store(tmp_path / "t.db")
    _baseline(s, n=6, cost=2.0, tools=20)
    _session(s, "wild", "proj", "claude-code", days_ago=0, cost=50.0, tools=20)
    flags = an.flag_unusual(s)
    assert len(flags) == 1
    assert flags[0].session_id == "wild"
    assert "cost" in flags[0].reason
    s.close()


def test_flags_tool_call_outlier(tmp_path):
    s = Store(tmp_path / "t.db")
    _baseline(s, n=6, cost=2.0, tools=20)
    _session(s, "loopy", "proj", "claude-code", days_ago=0, cost=2.0, tools=500)
    flags = an.flag_unusual(s)
    assert len(flags) == 1
    assert "tool calls" in flags[0].reason
    s.close()


def test_normal_session_not_flagged(tmp_path):
    s = Store(tmp_path / "t.db")
    _baseline(s, n=6, cost=2.0, tools=20)
    _session(s, "ok", "proj", "claude-code", days_ago=0, cost=2.5, tools=25)
    assert an.flag_unusual(s) == []
    s.close()


def test_baselines_are_per_project_and_harness(tmp_path):
    s = Store(tmp_path / "t.db")
    _baseline(s, n=6, cost=2.0, tools=20)
    # Same shape of outlier, but in a project with no baseline -> no flag.
    _session(s, "other-proj", "other", "codex", days_ago=0, cost=50.0, tools=500)
    assert an.flag_unusual(s) == []
    s.close()


def test_tiny_costs_dont_alarm(tmp_path):
    s = Store(tmp_path / "t.db")
    _baseline(s, n=6, cost=0.01, tools=20)
    _session(s, "cheap", "proj", "claude-code", days_ago=0, cost=0.05, tools=20)
    # 5x median, but below the $1 floor -> no flag
    assert an.flag_unusual(s) == []
    s.close()
