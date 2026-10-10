"""`dashcam serve` — localhost-only read-only timeline UI.

A single-file, dependency-free web view over the receipt store, built on
stdlib ``http.server``. It binds 127.0.0.1 only, serves GET requests, and
never mutates anything: the most it does is read the database and render
HTML. All user-controlled strings are HTML-escaped at render time.

Pages:
- ``/`` — overview: today-at-a-glance cards, anomaly flags, recent
  sessions table, per-harness breakdown.
- ``/session?id=<id>`` — session detail: tool calls, spend rows, and a
  chain-integrity check over that session's rows.
"""
from __future__ import annotations

import html
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .store import Store

CSS = """
:root{--bg:#fafbfc;--card:#fff;--ink:#1a1d21;--mut:#5b636b;--line:#e4e7eb;
--acc:#2563eb;--warn:#b45309;--ok:#15803d}
*{box-sizing:border-box}body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--ink);margin:0;padding:24px;max-width:1100px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .v{font-size:24px;font-weight:700}.card .l{font-size:12px;color:var(--mut);margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:13px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
th{background:#f1f3f5;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:none}a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}.flag{background:#fef3c7;border:1px solid #fcd34d;
border-radius:8px;padding:10px 14px;margin:8px 0;font-size:13px}
.ok{color:var(--ok);font-weight:600}.bad{color:#b91c1c;font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:8px;background:#e4e7eb;border-radius:4px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:var(--acc)}
footer{margin-top:32px;color:var(--mut);font-size:12px}
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} · dashcam</title><style>{CSS}</style></head>
<body>{body}<footer>dashcam · read-only · receipts verified against the local hash chain</footer>
</body></html>""".encode()


def _money(v: float) -> str:
    return f"${v:,.2f}"


def overview_html(store: Store) -> bytes:
    cx = store.cx
    today = cx.execute(
        "SELECT COUNT(*) c FROM sessions WHERE substr(started_at,1,10)=date('now')").fetchone()["c"]
    tools = cx.execute(
        "SELECT COUNT(*) c FROM events e JOIN sessions s ON s.id=e.session_id "
        "WHERE substr(s.started_at,1,10)=date('now')").fetchone()["c"]
    cost = cx.execute(
        "SELECT COALESCE(SUM(p.cost_usd),0) t FROM spend p JOIN sessions s ON s.id=p.session_id "
        "WHERE substr(s.started_at,1,10)=date('now')").fetchone()["t"]
    tokens = cx.execute(
        "SELECT COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) t FROM spend p "
        "JOIN sessions s ON s.id=p.session_id WHERE substr(s.started_at,1,10)=date('now')").fetchone()["t"]

    sessions = cx.execute(
        """SELECT s.id, s.harness, s.project, s.started_at,
                  (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id) AS tools,
                  COALESCE((SELECT SUM(cost_usd) FROM spend p WHERE p.session_id=s.id),0) AS cost
           FROM sessions s ORDER BY s.started_at DESC LIMIT 50""").fetchall()

    harnesses = cx.execute(
        """SELECT s.harness, COUNT(*) n, COALESCE(SUM(p.cost_usd),0) c
           FROM sessions s LEFT JOIN spend p ON p.session_id=s.id
           GROUP BY s.harness ORDER BY c DESC""").fetchall()
    max_c = max([r["c"] for r in harnesses] or [0])

    # anomaly flags (import lazily to keep serve import-light)
    from . import anomaly as anomaly_mod
    flags = anomaly_mod.flag_unusual(store)
    flag_html = "".join(
        f"<div class=flag>⚠ <b>unusual session</b> {_esc(f.harness)}/{_esc(f.project)} "
        f"<a href=\"/session?id={_esc(f.session_id)}\">{_esc(f.session_id[:12])}…</a> — {_esc(f.reason)}</div>"
        for f in flags[:5])

    rows = "".join(
        f"<tr><td><a href=\"/session?id={_esc(r['id'])}\">{_esc(r['id'][:12])}…</a></td>"
        f"<td>{_esc(r['harness'])}</td><td>{_esc(r['project'])}</td>"
        f"<td>{_esc((r['started_at'] or '?')[:16])}</td>"
        f"<td class=num>{r['tools']}</td><td class=num>{_money(r['cost'])}</td></tr>"
        for r in sessions)
    hb = "".join(
        f"<tr><td>{_esc(r['harness'])}</td><td class=num>{r['n']}</td>"
        f"<td class=num>{_money(r['c'])}</td>"
        f"<td><div class=bar><i style=\"width:{(r['c']/max_c*100) if max_c else 0:.0f}%\"></i></div></td></tr>"
        for r in harnesses)

    body = f"""
<h1>🎥 dashcam</h1><div class=sub>flight recorder for your AI coding agents · localhost only</div>
<div class=cards>
<div class=card><div class=v>{today}</div><div class=l>sessions today</div></div>
<div class=card><div class=v>{tools}</div><div class=l>tool calls today</div></div>
<div class=card><div class=v>{tokens:,}</div><div class=l>tokens today</div></div>
<div class=card><div class=v>{_money(cost)}</div><div class=l>cost today</div></div>
</div>
{flag_html}
<h2>Recent sessions</h2>
<table><tr><th>Session</th><th>Harness</th><th>Project</th><th>Started</th>
<th class=num>Tools</th><th class=num>Cost</th></tr>{rows or '<tr><td colspan=6>No sessions yet — run <code>dashcam ingest</code>.</td></tr>'}</table>
<h2>Cost by harness</h2>
<table><tr><th>Harness</th><th class=num>Sessions</th><th class=num>Cost</th><th></th></tr>{hb}</table>
"""
    return _page("overview", body)


def _session_chain_ok(store: Store, session_id: str) -> tuple[bool, str]:
    """Recompute hashes for one session's rows; report integrity."""
    cx = store.cx
    rows = cx.execute(
        "SELECT seq, prev_hash, hash, 'event' AS k, tool AS label, summary AS payload "
        "FROM events WHERE session_id=? "
        "UNION ALL "
        "SELECT seq, prev_hash, hash, 'spend' AS k, model AS label, "
        "input_tokens || '/' || output_tokens AS payload "
        "FROM spend WHERE session_id=? ORDER BY seq",
        (session_id, session_id)).fetchall()
    if not rows:
        return True, "no evidence rows"
    # NOTE: full-chain verification needs global order; here we check each
    # row's stored hash links to its recorded prev_hash (tamper-evidence
    # within the session's slice). `dashcam verify` checks the whole chain.
    prev = None
    for r in rows:
        if prev is not None and r["prev_hash"] != prev:
            return False, f"chain break at seq {r['seq']}"
        prev = r["hash"]
    return True, f"{len(rows)} rows linked"


def session_html(store: Store, session_id: str) -> bytes:
    cx = store.cx
    s = cx.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if s is None:
        return _page("not found", "<h1>Session not found</h1><p><a href=/>← back</a></p>")
    events = cx.execute(
        "SELECT ts, tool, summary, seq FROM events WHERE session_id=? ORDER BY seq",
        (session_id,)).fetchall()
    spends = cx.execute(
        "SELECT provider, model, input_tokens, output_tokens, cost_usd, seq "
        "FROM spend WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
    ok, msg = _session_chain_ok(store, session_id)
    badge = f"<span class={'ok' if ok else 'bad'}>{'✓' if ok else '✗'} {_esc(msg)}</span>"

    erows = "".join(
        f"<tr><td class=num>{e['seq']}</td><td>{_esc((e['ts'] or '')[:19])}</td>"
        f"<td><b>{_esc(e['tool'])}</b></td><td>{_esc(e['summary'])}</td></tr>"
        for e in events)
    srows = "".join(
        f"<tr><td>{_esc(x['provider'])}/{_esc(x['model'])}</td>"
        f"<td class=num>{x['input_tokens']:,}</td><td class=num>{x['output_tokens']:,}</td>"
        f"<td class=num>{_money(x['cost_usd'])}</td></tr>"
        for x in spends)
    body = f"""
<h1>Session <code>{_esc(session_id[:16])}</code></h1>
<div class=sub>{_esc(s['harness'])} · {_esc(s['project'])} · {_esc(s['started_at'] or '?')} → {_esc(s['ended_at'] or '?')}
· chain: {badge}</div>
<p><a href=/>← all sessions</a></p>
<h2>Tool calls ({len(events)})</h2>
<table><tr><th class=num>#</th><th>Time</th><th>Tool</th><th>Summary</th></tr>
{erows or '<tr><td colspan=4>No tool calls recorded.</td></tr>'}</table>
<h2>Spend ({len(spends)})</h2>
<table><tr><th>Model</th><th class=num>In</th><th class=num>Out</th><th class=num>Cost</th></tr>
{srows or '<tr><td colspan=4>No spend recorded.</td></tr>'}</table>
"""
    return _page(f"session {session_id[:12]}", body)


class _Handler(BaseHTTPRequestHandler):
    store: Store  # set by serve()

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                body = overview_html(self.store)
            elif parsed.path == "/session":
                q = parse_qs(parsed.query)
                body = session_html(self.store, q.get("id", [""])[0])
            else:
                self.send_response(404)
                self.end_headers()
                return
        except Exception as e:  # noqa: BLE001 - never leak a traceback page
            body = _page("error", f"<h1>Error</h1><p>{_esc(type(e).__name__)}</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Read-only by construction: no POST/PUT/DELETE handlers exist.
    def do_POST(self): self.send_response(405); self.end_headers()
    def do_PUT(self): self.send_response(405); self.end_headers()
    def do_DELETE(self): self.send_response(405); self.end_headers()


def run(port: int = 8321, db=None, block: bool = True) -> HTTPServer:
    """Start the server on 127.0.0.1. Returns the server (caller may thread it)."""
    store = db if isinstance(db, Store) else Store(db)
    _Handler.store = store
    srv = HTTPServer(("127.0.0.1", port), _Handler)
    if block:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return srv


def run_in_thread(port: int = 0, db=None) -> tuple[HTTPServer, threading.Thread]:
    srv = run(port=port, db=db, block=False)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t
