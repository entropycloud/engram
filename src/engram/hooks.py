"""Signal capture helpers for Claude Code hooks.

These functions are called by Claude Code hooks and must be fast (< 100ms target).
They append metric events to JSONL sidecars without loading the full store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from engram.evaluator import EngramEvaluator
from engram.models import MetricEvent
from engram.store import EngramStore


def record_signal(
    store_root: Path,
    slug: str,
    event_type: str,
    session: str,
    context: str | None = None,
    detail: str | None = None,
) -> None:
    """Record a usage signal for an engram. Appends to JSONL sidecar."""
    store = EngramStore(store_root)
    evaluator = EngramEvaluator(store)
    event = MetricEvent(
        ts=datetime.now(tz=UTC),
        event=event_type,  # type: ignore[arg-type]
        session=session,
        context=context,
        detail=detail,
    )
    evaluator.append_event(slug, event)


def record_session_end(
    store_root: Path,
    session: str,
    slugs: list[str],
    outcome: str,
) -> None:
    """Record session end signals for all engrams used in the session."""
    store = EngramStore(store_root)
    evaluator = EngramEvaluator(store)
    now = datetime.now(tz=UTC)
    for slug in slugs:
        event = MetricEvent(
            ts=now,
            event=outcome,  # type: ignore[arg-type]
            session=session,
        )
        evaluator.append_event(slug, event)


def record_feedback(
    store_root: Path,
    slug: str,
    session: str,
    rating: str,
) -> None:
    """Record explicit user feedback (up/down) for an engram."""
    store = EngramStore(store_root)
    evaluator = EngramEvaluator(store)
    event = MetricEvent(
        ts=datetime.now(tz=UTC),
        event="feedback",
        session=session,
        rating=rating,  # type: ignore[arg-type]
    )
    evaluator.append_event(slug, event)
