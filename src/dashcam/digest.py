"""The `dashcam today` digest — the daily habit.

Reads the local receipt store and renders what your agents did: sessions,
tool calls, files touched, tokens, and cost per provider/model. If there is
nothing recorded yet, it says so honestly and points at `dashcam demo`.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .store import Store


def _today_prefix() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def day_stats(store: Store, day: str | None = None) -> dict:
    day = day or _today_prefix()
    cx = store.cx
    sessions = cx.execute(
        "SELECT * FROM sessions WHERE substr(started_at,1,10)=? OR substr(started_at,1,10)=?",
        (day, day.replace("-", "")),
    ).fetchall()
    # Fallback: match on local-date prefix more loosely (transcripts use ISO timestamps).
    if not sessions:
        sessions = cx.execute(
            "SELECT * FROM sessions WHERE started_at LIKE ?", (day + "%",)
        ).fetchall()
    sids = [s["id"] for s in sessions]
    events: list = []
    spend: list = []
    if sids:
        q = ",".join("?" for _ in sids)
        events = cx.execute(f"SELECT * FROM events WHERE session_id IN ({q})", sids).fetchall()
        spend = cx.execute(f"SELECT * FROM spend WHERE session_id IN ({q})", sids).fetchall()

    tools = Counter(e["tool"] for e in events)
    files: Counter[str] = Counter()
    for e in events:
        try:
            detail = json.loads(e["detail"] or "{}")
        except json.JSONDecodeError:
            continue
        fp = detail.get("file_path")
        if fp:
            files[fp] += 1

    by_model: dict[str, dict] = {}
    total_in = total_out = 0
    total_cost = 0.0
    for s in spend:
        key = f"{s['provider']}/{s['model']}"
        b = by_model.setdefault(key, {"in": 0, "out": 0, "cost": 0.0})
        b["in"] += s["input_tokens"]
        b["out"] += s["output_tokens"]
        b["cost"] += s["cost_usd"]
        total_in += s["input_tokens"]
        total_out += s["output_tokens"]
        total_cost += s["cost_usd"]

    by_harness = Counter(s["harness"] for s in sessions)
    # Anomaly flags: sessions deviating from their (project, harness) baseline.
    # Imported lazily to keep the digest import-light for tests.
    from . import anomaly as anomaly_mod
    flags = [f for f in anomaly_mod.flag_unusual(store)
             if f.session_id in {s["id"] for s in sessions}]
    return {
        "day": day, "sessions": len(sessions), "by_harness": by_harness,
        "events": len(events), "tools": tools, "files": files,
        "by_model": by_model, "total_in": total_in, "total_out": total_out,
        "total_cost": total_cost, "flags": flags,
    }


def render_digest(stats: dict, console: Console | None = None) -> None:
    console = console or Console()
    if not stats["sessions"]:
        console.print("[bold]dashcam — no sessions recorded yet.[/bold]")
        console.print("Run [cyan]dashcam demo[/cyan] to see what a digest looks like,")
        console.print("or [cyan]dashcam ingest[/cyan] to read your agents' session logs.")
        return
    h = " · ".join(f"{k} {v}" for k, v in stats["by_harness"].items())
    console.print(f"[bold]dashcam — {stats['day']}[/bold]  ({h})")
    console.print(f"Sessions: [bold]{stats['sessions']}[/bold]   "
                  f"Tool calls: [bold]{stats['events']}[/bold]   "
                  f"Tokens: [bold]{stats['total_in']:,}[/bold] in / "
                  f"[bold]{stats['total_out']:,}[/bold] out   "
                  f"Cost: [bold green]${stats['total_cost']:.2f}[/bold green]")
    flags = stats.get("flags") or []
    if flags:
        console.print()
        for f in flags[:5]:
            console.print(f"[yellow bold]⚠ unusual session[/yellow bold] "
                          f"[dim]{f.harness}/{f.project}[/dim] {f.session_id[:12]}… — {f.reason}")
        if len(flags) > 5:
            console.print(f"[dim]… and {len(flags) - 5} more[/dim]")
    if stats["tools"]:
        t = Table(title="Tool calls", show_header=True, header_style="bold")
        t.add_column("Tool"); t.add_column("Count", justify="right")
        for tool, n in stats["tools"].most_common(8):
            t.add_row(tool, str(n))
        console.print(t)
    if stats["files"]:
        t = Table(title="Most touched files", show_header=True, header_style="bold")
        t.add_column("File"); t.add_column("Touches", justify="right")
        for fp, n in stats["files"].most_common(8):
            t.add_row(fp, str(n))
        console.print(t)
    if stats["by_model"]:
        t = Table(title="Cost by model", show_header=True, header_style="bold")
        t.add_column("Provider/Model"); t.add_column("In", justify="right")
        t.add_column("Out", justify="right"); t.add_column("Cost", justify="right")
        for k, b in sorted(stats["by_model"].items(), key=lambda kv: -kv[1]["cost"]):
            t.add_row(k, f"{b['in']:,}", f"{b['out']:,}", f"${b['cost']:.2f}")
        console.print(t)
    console.print("[dim]Receipts are hash-chained locally. `dashcam verify` checks the tape.[/dim]")
