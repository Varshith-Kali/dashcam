"""Ed25519-signed portable receipt bundles.

The hash chain proves the local database wasn't tampered with *in place*,
but it can't leave the machine: verification needs the DB. Signed bundles
close that gap. ``dashcam init`` creates a local Ed25519 keypair (private
key at ``~/.dashcam/signing.key``, mode 0600). ``dashcam export --format
bundle`` emits a self-contained JSON bundle — the session's canonical
receipts plus a signature and the public key — that anyone can verify
offline with ``dashcam verify-bundle``. That's the artifact you hand to
an auditor, a client, or your SOC: proof of what the agent did, signed by
the machine that recorded it.

Threat model honesty: the private key lives next to the database it
signs, so this proves *provenance* (these receipts came from this
dashcam identity, unaltered since signing) — not protection against an
attacker who owned the whole machine before signing. For that, keep
offline copies of bundles.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

if TYPE_CHECKING:
    from .store import Store

BUNDLE_VERSION = 1


def key_paths(home: Path | None = None) -> tuple[Path, Path]:
    d = (home or Path.home()) / ".dashcam"
    return d / "signing.key", d / "signing.pub"


def get_or_create_keypair(home: Path | None = None) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Load the local signing keypair, creating it on first use."""
    priv_path, _ = key_paths(home)
    if priv_path.exists():
        raw = priv_path.read_bytes()
        return (Ed25519PrivateKey.from_private_bytes(raw),
                Ed25519PrivateKey.from_private_bytes(raw).public_key())
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(priv_path.parent, 0o700)
        except OSError:
            pass
    private = Ed25519PrivateKey.generate()
    priv_path.write_bytes(private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()))
    if os.name != "nt":
        try:
            os.chmod(priv_path, 0o600)
        except OSError:
            pass
    return private, private.public_key()


def public_key_hex(public: Ed25519PublicKey) -> str:
    return public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def export_bundle(store: Store, session_id: str,
                  private: Ed25519PrivateKey) -> dict:
    """Build a signed bundle for one session."""
    cur = store.cx.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"no such session: {session_id}")
    session = dict(row)
    events = [dict(r) for r in store.cx.execute(
        "SELECT session_id, ts, tool, summary, detail, seq, hash "
        "FROM events WHERE session_id = ? ORDER BY seq", (session_id,))]
    spends = [dict(r) for r in store.cx.execute(
        "SELECT session_id, provider, model, input_tokens, output_tokens, "
        "cost_usd, seq, hash FROM spend WHERE session_id = ? ORDER BY seq",
        (session_id,))]
    body = {"version": BUNDLE_VERSION, "session": session,
            "events": events, "spends": spends,
            "public_key": public_key_hex(private.public_key())}
    sig = private.sign(_canonical(body))
    body["signature"] = sig.hex()
    return body


def verify_bundle(bundle: dict) -> tuple[bool, str]:
    """Verify a bundle's signature. Returns (ok, message)."""
    bundle = dict(bundle)
    sig_hex = bundle.pop("signature", None)
    pub_hex = bundle.get("public_key")
    if not sig_hex or not pub_hex:
        return False, "bundle missing signature or public_key"
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        public.verify(bytes.fromhex(sig_hex), _canonical(bundle))
    except (ValueError, TypeError, InvalidSignature) as e:
        return False, f"signature invalid: {e}"
    n_ev = len(bundle.get("events", []))
    n_sp = len(bundle.get("spends", []))
    sid = bundle.get("session", {}).get("id", "?")
    return True, f"valid signature from {pub_hex[:16]}… for session {sid} ({n_ev} tool calls, {n_sp} spend rows)"
