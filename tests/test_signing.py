"""Signing tests: bundles prove provenance; tampering breaks verification."""
from dashcam import signing as sg
from dashcam.models import Session, Spend, ToolEvent
from dashcam.store import Store


def _seeded(tmp_path):
    s = Store(tmp_path / "t.db")
    s.record_session(Session(id="s1", harness="claude-code", project="proj",
                             started_at="2026-10-08T09:00:00Z"))
    s.record_event(ToolEvent(session_id="s1", ts="2026-10-08T09:01:00Z",
                             tool="Bash", summary="$ pytest -q"))
    s.record_spend(Spend(session_id="s1", provider="anthropic", model="m",
                         input_tokens=1000, output_tokens=100, cost_usd=0.5))
    return s


def test_keypair_creation_and_reload(tmp_path):
    home = tmp_path / "home"
    _priv, pub = sg.get_or_create_keypair(home)
    _priv2, pub2 = sg.get_or_create_keypair(home)
    assert (sg.public_key_hex(pub) == sg.public_key_hex(pub2))
    priv_path, _ = sg.key_paths(home)
    assert priv_path.exists()
    import os
    if os.name != "nt":
        assert oct(priv_path.stat().st_mode)[-3:] == "600"


def test_bundle_roundtrip(tmp_path):
    s = _seeded(tmp_path)
    priv, _ = sg.get_or_create_keypair(tmp_path / "home")
    bundle = sg.export_bundle(s, "s1", priv)
    assert bundle["session"]["id"] == "s1"
    assert len(bundle["events"]) == 1
    assert len(bundle["spends"]) == 1
    assert "signature" in bundle and "public_key" in bundle
    ok, msg = sg.verify_bundle(bundle)
    assert ok, msg
    assert "s1" in msg
    s.close()


def test_bundle_tamper_detected(tmp_path):
    s = _seeded(tmp_path)
    priv, _ = sg.get_or_create_keypair(tmp_path / "home")
    bundle = sg.export_bundle(s, "s1", priv)
    bundle["events"][0]["summary"] = "$ rm -rf /  # forged"
    ok, _msg = sg.verify_bundle(bundle)
    assert not ok
    s.close()


def test_bundle_wrong_key_rejected(tmp_path):
    s = _seeded(tmp_path)
    priv, _ = sg.get_or_create_keypair(tmp_path / "home")
    bundle = sg.export_bundle(s, "s1", priv)
    _, other_pub = sg.get_or_create_keypair(tmp_path / "home2")
    bundle["public_key"] = sg.public_key_hex(other_pub)
    ok, _ = sg.verify_bundle(bundle)
    assert not ok
    s.close()


def test_bundle_missing_session_raises(tmp_path):
    s = _seeded(tmp_path)
    priv, _ = sg.get_or_create_keypair(tmp_path / "home")
    try:
        sg.export_bundle(s, "nope", priv)
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")
    s.close()
