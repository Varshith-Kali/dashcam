"""serve tests: the UI is read-only, local-only, and escapes user data."""
import urllib.request

from dashcam import serve as serve_mod
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store


def _seeded(tmp_path):
    s = Store(tmp_path / "t.db")
    s.record_session(Session(id="sess-1", harness="claude-code", project="proj",
                             started_at="2026-10-08T09:00:00Z"))
    s.record_event(ToolEvent(session_id="sess-1", ts="2026-10-08T09:01:00Z",
                             tool="Bash", summary="$ pytest -q <script>alert(1)</script>"))
    s.record_spend(Spend(session_id="sess-1", provider="anthropic", model="m",
                         input_tokens=1000, output_tokens=100, cost_usd=0.5))
    return s


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.read().decode()


def test_overview_renders(tmp_path):
    s = _seeded(tmp_path)
    srv, _ = serve_mod.run_in_thread(port=0, db=s)
    try:
        port = srv.server_address[1]
        status, body = _get(port, "/")
        assert status == 200
        assert "dashcam" in body
        assert "sess-1"[:12] in body
        assert "claude-code" in body
    finally:
        srv.shutdown()
        s.close()


def test_session_detail_escapes_and_shows_chain(tmp_path):
    s = _seeded(tmp_path)
    srv, _ = serve_mod.run_in_thread(port=0, db=s)
    try:
        port = srv.server_address[1]
        status, body = _get(port, "/session?id=sess-1")
        assert status == 200
        # user data escaped, chain badge present
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert "rows linked" in body
        assert "$0.50" in body
    finally:
        srv.shutdown()
        s.close()


def test_unknown_paths_and_methods_rejected(tmp_path):
    s = _seeded(tmp_path)
    srv, _ = serve_mod.run_in_thread(port=0, db=s)
    try:
        port = srv.server_address[1]
        try:
            _get(port, "/nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=b"x")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 405")
        except urllib.error.HTTPError as e:
            assert e.code == 405
    finally:
        srv.shutdown()
        s.close()


def test_binds_localhost_only(tmp_path):
    s = _seeded(tmp_path)
    srv, _ = serve_mod.run_in_thread(port=0, db=s)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.shutdown()
        s.close()
