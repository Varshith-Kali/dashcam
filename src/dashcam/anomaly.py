"""Anomaly detection over session receipts.

Deliberately explainable statistics — no ML models, no API calls, nothing
to configure. For each (project, harness) pair, dashcam keeps a trailing
baseline of per-session cost and tool-call counts from the last 30 days.
A session is flagged when it costs or calls tools at more than 3x the
baseline median, with minimum floors so tiny baselines don't alarm on
noise.

Baselines are median-based (robust to the very outliers we're hunting)
and require at least 5 sessions before any flagging, so a new project
doesn't cry wolf on its second session.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

BASELINE_DAYS = 30
MIN_SESSIONS = 5
RATIO_THRESHOLD = 3.0
MIN_COST_USD = 1.0  # don't flag sessions cheaper than this on cost
MIN_TOOLS = 10  # don't flag sessions with fewer tool calls than this


@dataclass
class Baseline:
    project: str
    harness: str
    n_sessions: int
    median_cost: float
    median_tools: float


@dataclass
class Flag:
    session_id: str
    project: str
    harness: str
    cost: float
    tool_calls: int
    median_cost: float
    median_tools: float
    reason: str


def _session_stats(store: Store, days: int) -> list[dict]:
    """Per-session cost + tool-call counts for sessions started in window."""
    cur = store.cx.execute(
        """
        SELECT s.id, s.project, s.harness,
               COALESCE(sp.cost, 0) AS cost,
               COALESCE(ev.tools, 0) AS tools
        FROM sessions s
        LEFT JOIN (SELECT session_id, SUM(cost_usd) AS cost
                   FROM spend GROUP BY session_id) sp ON sp.session_id = s.id
        LEFT JOIN (SELECT session_id, COUNT(*) AS tools
                   FROM events GROUP BY session_id) ev ON ev.session_id = s.id
        WHERE s.started_at >= datetime('now', ?)
        """,
        (f"-{days} days",),
    )
    return [dict(r) for r in cur.fetchall()]


def baselines(store: Store, days: int = BASELINE_DAYS) -> dict[tuple[str, str], Baseline]:
    """Compute per-(project, harness) baselines from recent sessions."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in _session_stats(store, days):
        groups.setdefault((row["project"], row["harness"]), []).append(row)
    out: dict[tuple[str, str], Baseline] = {}
    for (project, harness), rows in groups.items():
        if len(rows) < MIN_SESSIONS:
            continue
        out[(project, harness)] = Baseline(
            project=project, harness=harness, n_sessions=len(rows),
            median_cost=median([r["cost"] for r in rows]),
            median_tools=median([r["tools"] for r in rows]),
        )
    return out


def flag_unusual(store: Store, days: int = BASELINE_DAYS) -> list[Flag]:
    """Flag recent sessions that deviate sharply from their baseline."""
    base = baselines(store, days)
    flags: list[Flag] = []
    for row in _session_stats(store, days):
        b = base.get((row["project"], row["harness"]))
        if b is None:
            continue
        reasons: list[str] = []
        if (row["cost"] >= MIN_COST_USD and b.median_cost > 0
                and row["cost"] > b.median_cost * RATIO_THRESHOLD):
            reasons.append(f"cost ${row['cost']:.2f} vs median ${b.median_cost:.2f}")
        if (row["tools"] >= MIN_TOOLS and b.median_tools > 0
                and row["tools"] > b.median_tools * RATIO_THRESHOLD):
            reasons.append(f"{row['tools']} tool calls vs median {b.median_tools:.0f}")
        if reasons:
            flags.append(Flag(
                session_id=row["id"], project=row["project"], harness=row["harness"],
                cost=row["cost"], tool_calls=row["tools"],
                median_cost=b.median_cost, median_tools=b.median_tools,
                reason="; ".join(reasons)))
    return flags
