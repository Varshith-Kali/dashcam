"""Harness parser interface.

Every harness parser is a small module exposing:

- ``HARNESS``: short id, e.g. ``"claude-code"``
- ``iter_transcripts(base=None)``: yield ``(project, path)`` for each session log
- ``parse_transcript(project, path, skip_lines=0)``:
  return ``(session, events, spends, nlines)``

Parsers are **read-only** (never modify the harness logs), **total**
(malformed input is skipped, never fatal), and **tested against fixtures**
that mirror the real harness format. A parser whose format can't be
verified against real harness output doesn't ship — an unverified parser
is worse than none, because it silently produces wrong receipts.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from ..models import Session, Spend, ToolEvent


class HarnessParser(Protocol):
    HARNESS: str

    def iter_transcripts(self, base: Path | None = None) -> Iterator[tuple[str, Path]]: ...
    def parse_transcript(
        self, project: str, path: Path, skip_lines: int = 0
    ) -> tuple[Session, list[ToolEvent], list[Spend], int]: ...
