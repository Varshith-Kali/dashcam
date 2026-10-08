"""CLI-level tests: export formats and runs aggregation."""
import json

from typer.testing import CliRunner

from dashcam.cli import app
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store

runner = CliRunner()


def _seed(db_path):
    s = Store(db_path)
    s.record_session(Session(id="s1", harness="claude-code", project="proj",
                             started_at="2026-10-08T09:00:00.000Z"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-10-08T09:01:00.000Z",
                             tool="Bash", summary="$ pytest -q"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-10-08T09:02:00.000Z",
                             tool="Read", summary="Read a.py"))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                         input_tokens=1000, output_tokens=200, cost_usd=1.50))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                         input_tokens=2000, output_tokens=400, cost_usd=2.50))
    s.close()


def test_export_hec_lines_are_valid_hec(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    r = runner.invoke(app, ["export", "--db", str(db), "--format", "hec"])
    assert r.exit_code == 0, r.output
    lines = [l for l in r.output.splitlines() if l.strip()]
    assert len(lines) == 4  # 2 events + 2 spends
    kinds = set()
    for line in lines:
        hec = json.loads(line)
        assert hec["source"] == "dashcam"
        assert hec["sourcetype"] == "dashcam:receipt"
        assert hec.get("host")
        evt = hec["event"]
        kinds.add(evt["kind"])
        assert evt["session_id"] == "s1"
        assert evt["harness"] == "claude-code"
        assert "chain_seq" in evt and "chain_hash" in evt
    assert kinds == {"tool_call", "spend"}
    # tool_call events carry a proper epoch time
    tool_lines = [json.loads(l) for l in lines
                  if json.loads(l)["event"]["kind"] == "tool_call"]
    assert all(isinstance(x["time"], float) and x["time"] > 1_700_000_000
               for x in tool_lines)


def test_export_hec_rejects_bad_format(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    r = runner.invoke(app, ["export", "--db", str(db), "--format", "xml"])
    assert r.exit_code == 1


def test_runs_shows_correct_cost_no_fanout(tmp_path):
    """2 events x 2 spends must not multiply the sums (join fan-out)."""
    db = tmp_path / "t.db"
    _seed(db)
    r = runner.invoke(app, ["runs", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "$4.00" in r.output  # 1.50 + 2.50, not multiplied
    assert "3,000" in r.output  # 1000 + 2000 input tokens


def test_export_bundle_and_verify_bundle(tmp_path, monkeypatch):
    from dashcam import signing as sg
    db = tmp_path / "t.db"
    home = tmp_path / "home"
    monkeypatch.setattr(sg, "key_paths",
                        lambda h=None: (home / ".dashcam" / "signing.key",
                                        home / ".dashcam" / "signing.pub"))
    # seed via helper in this file
    _seed(db)
    r = runner.invoke(app, ["export", "--db", str(db), "--format", "bundle",
                            "--session", "s1"])
    assert r.exit_code == 0, r.output
    bundle = json.loads(r.output)
    assert bundle["session"]["id"] == "s1"
    bundle_file = tmp_path / "b.json"
    bundle_file.write_text(r.output)
    r2 = runner.invoke(app, ["verify-bundle", str(bundle_file)])
    assert r2.exit_code == 0, r2.output
    assert "valid signature" in r2.output


def test_export_bundle_needs_session(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    r = runner.invoke(app, ["export", "--db", str(db), "--format", "bundle"])
    assert r.exit_code == 1
