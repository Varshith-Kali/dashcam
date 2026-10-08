"""Core data models — plain dataclasses, no ORM, no magic."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Session:
    id: str
    harness: str  # claude-code | opencode | gemini-cli | demo
    project: str
    started_at: str | None = None
    ended_at: str | None = None
    source: str = "ingest"  # ingest | demo


@dataclass
class ToolEvent:
    session_id: str
    ts: str | None
    tool: str
    summary: str
    detail: str = ""  # JSON blob with structured fields (file, command, ...)
    source: str = "ingest"


@dataclass
class Spend:
    session_id: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    source: str = "ingest"
