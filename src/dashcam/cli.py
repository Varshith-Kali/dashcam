"""dashcam CLI — read-only. It records; it never restrains."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from . import __version__
from . import cost as cost_mod
from . import demo as demo_mod
from . import digest as digest_mod
from .ingest import claude_code
from .store import Store

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _store(db: str | None) -> Store:
    return Store(Path(db) if db else None)


@app.command()
def init(db: str = typer.Option(None, help="Path to the receipts database.")) -> None:
    """Set up local storage and show what dashcam will read."""
    store = _store(db)
    console.print(f"[green]dashcam ready.[/green] Receipts live in [cyan]{store.path}[/cyan]")
    dirs = claude_code.transcript_dirs()
    if dirs:
        console.print(f"Found Claude Code transcripts in {len(dirs)} project dir(s):")
        for d in dirs[:5]:
            console.print(f"  • {d}")
        if len(dirs) > 5:
            console.print(f"  … and {len(dirs) - 5} more")
    else:
        console.print("[yellow]No Claude Code transcripts found yet.[/yellow]")
    console.print("dashcam only [bold]reads[/bold] these logs. It never modifies them.")
    store.close()


@app.command()
def ingest(
    harness: str = typer.Option("claude-code", help="Which harness logs to read."),
    db: str = typer.Option(None, help="Path to the receipts database."),
) -> None:
    """Read harness session logs into the tamper-evident receipt store."""
    store = _store(db)
    if harness != "claude-code":
        console.print(f"[red]harness '{harness}' not supported yet (v0.1 reads claude-code).[/red]")
        raise typer.Exit(1)
    pricing = cost_mod.load_pricing()
    n_sessions = n_events = n_spend = 0
    warned: set[str] = set()
    for project, path in claude_code.iter_transcripts():
        offset = store.file_offset(str(path))
        session, events, spends, nlines = claude_code.parse_transcript(project, path,
                                                                      skip_lines=offset)
        if not store.has_session(session.id):
            store.record_session(session)
            n_sessions += 1
        for e in events:
            store.record_event(e)
            n_events += 1
        for s in spends:
            s.cost_usd, known = cost_mod.cost_usd(s.provider, s.model, s.input_tokens,
                                                 s.output_tokens, pricing)
            if not known and s.model not in warned:
                warned.add(s.model)
                console.print(f"[yellow]no price for model '{s.model}' — cost recorded as $0 "
                              f"(PRs to pricing/models.json welcome)[/yellow]")
            store.record_spend(s)
            n_spend += 1
        store.set_file_offset(str(path), nlines)
    console.print(f"ingested [bold]{n_sessions}[/bold] new sessions, "
                  f"[bold]{n_events}[/bold] tool calls, [bold]{n_spend}[/bold] spend rows")
    store.close()


@app.command()
def today(db: str = typer.Option(None, help="Path to the receipts database.")) -> None:
    """Show what your agents did today — sessions, tool calls, tokens, cost."""
    store = _store(db)
    digest_mod.render_digest(digest_mod.day_stats(store), console)
    store.close()


@app.command(name="runs")
def list_runs(db: str = typer.Option(None, help="Path to the receipts database."),
              limit: int = typer.Option(20, help="How many sessions to show.")) -> None:
    """List recorded sessions."""
    store = _store(db)
    rows = store.cx.execute(
        "SELECT id, harness, project, started_at, source FROM sessions "
        "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        console.print("No sessions recorded. Try [cyan]dashcam demo[/cyan] or [cyan]dashcam ingest[/cyan].")
    for r in rows:
        console.print(f"{r['started_at'] or '?':25} {r['harness']:12} {r['project']:30} "
                      f"[dim]{r['id'][:12]}[/dim]")
    store.close()


@app.command()
def verify(db: str = typer.Option(None, help="Path to the receipts database.")) -> None:
    """Verify the hash chain — prove the tape wasn't tampered with."""
    store = _store(db)
    ok, msg = store.verify()
    console.print(f"[{'green' if ok else 'red bold'}]{msg}[/]")
    store.close()
    if not ok:
        raise typer.Exit(1)


@app.command()
def demo(db: str = typer.Option(None, help="Path to the receipts database."),
         clean: bool = typer.Option(False, help="Remove demo rows instead.")) -> None:
    """Record a synthetic demo session and show the digest (first-run tour)."""
    store = _store(db)
    if clean:
        n = demo_mod.clean_demo(store)
        console.print(f"removed {n} demo session(s)")
    else:
        demo_mod.run_demo(store, console)
    store.close()


@app.command()
def version() -> None:
    console.print(f"dashcam {__version__}")


if __name__ == "__main__":
    app()
