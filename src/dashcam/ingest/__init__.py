"""Read-only transcript ingestors — one per harness.

Each ingestor reads the session logs the harness *already writes* and yields
normalized (Session, ToolEvent, Spend) records. Nothing is intercepted,
nothing is modified, nothing phones home. If a harness changes its log
format, the parser degrades gracefully (skips what it can't parse) rather
than crashing.
"""
