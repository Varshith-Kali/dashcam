"""Read-only parser for Claude Code transcripts.

Source: ``~/.claude/projects/<project-slug>/<session-id>.jsonl`` — the
harness's own record. Each line is a message envelope; assistant messages
carry ``content`` blocks (text / tool_use) and ``usage`` token counts.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .. import redact as redact_mod
from ..models import Session, Spend, ToolEvent

HARNESS = "claude-code"


def transcript_dirs(base: Path | None = None) -> list[Path]:
    root = base or (Path.home() / ".claude" / "projects")
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def iter_transcripts(base: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Yield (project_slug, transcript_path) for every session file."""
    for d in transcript_dirs(base):
        for f in sorted(d.glob("*.jsonl")):
            yield d.name, f


def summarize_tool(name: str, tool_input: dict) -> tuple[str, dict]:
    """Human summary + structured detail for a tool call."""
    if not isinstance(tool_input, dict):
        tool_input = {}
    if name == "Bash":
        cmd = str(tool_input.get("command", ""))[:160]
        return f"$ {cmd}", {"command": cmd}
    if name in ("Read", "Write", "Edit", "NotebookRead", "NotebookEdit"):
        fp = str(tool_input.get("file_path", "?"))
        return f"{name} {fp}", {"file_path": fp}
    if name == "Glob":
        return f"Glob {tool_input.get('pattern', '')}", {"pattern": str(tool_input.get("pattern", ""))}
    if name == "Grep":
        return f"Grep {tool_input.get('pattern', '')}", {"pattern": str(tool_input.get("pattern", ""))}
    if name == "TodoWrite":
        todos = tool_input.get("todos", [])
        return f"TodoWrite ({len(todos)} items)", {"count": len(todos)}
    if name == "Task":
        return f"Task: {str(tool_input.get('description', ''))[:80]}", {
            "description": str(tool_input.get("description", ""))[:200]}
    blob = json.dumps(tool_input, separators=(",", ":"))[:160]
    return f"{name} {blob}", {"raw": blob}


def detect_provider(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "codex")):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    if m.startswith("deepseek"):
        return "deepseek"
    return "unknown"


def parse_transcript(project: str, path: Path, skip_lines: int = 0):
    """Parse one transcript file.

    Returns (session, events, spends). Malformed lines are skipped, never fatal.
    """
    session_id = path.stem
    events: list[ToolEvent] = []
    spends: list[Spend] = []
    started_at: str | None = None
    ended_at: str | None = None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[skip_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            env = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(env, dict):
            continue
        ts = env.get("timestamp")
        if ts:
            started_at = started_at or ts
            ended_at = ts
        if env.get("type") != "assistant":
            continue
        msg = env.get("message")
        if not isinstance(msg, dict):
            continue
        model = env.get("model") or msg.get("model") or ""
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        def _tokens(v) -> int:
            try:
                n = int(v or 0)
            except (TypeError, ValueError):
                return 0
            return max(n, 0)

        in_tok = _tokens(usage.get("input_tokens"))
        out_tok = _tokens(usage.get("output_tokens"))
        if in_tok or out_tok:
            spends.append(Spend(session_id=session_id, provider=detect_provider(model),
                                model=model or "unknown",
                                input_tokens=in_tok, output_tokens=out_tok))
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name", "unknown"))
            summary, detail = summarize_tool(name, block.get("input") or {})
            # Redact before persisting: summaries keep command text, which can
            # carry pasted secrets.
            events.append(ToolEvent(session_id=session_id, ts=ts, tool=name,
                                    summary=redact_mod.redact(summary),
                                    detail=redact_mod.redact(json.dumps(detail))))
    session = Session(id=session_id, harness=HARNESS, project=project,
                      started_at=started_at, ended_at=ended_at)
    return session, events, spends, len(lines)
