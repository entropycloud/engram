"""Signal capture helpers for Claude Code hooks.

These functions are called by Claude Code hooks and must be fast (< 100ms target).
They append metric events to JSONL sidecars without loading the full store.
"""

from __future__ import annotations

import json
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


def _injection_path(store_root: Path, session: str) -> Path:
    """Path to session injection tracking file."""
    return store_root / "metrics" / f"_inj_{session}.jsonl"


def record_injection(
    store_root: Path,
    session: str,
    slugs: list[str],
) -> None:
    """Record which engrams were injected in a session. Fast append."""
    path = _injection_path(store_root, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "ts": datetime.now(tz=UTC).isoformat(),
        "slugs": slugs,
    }) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def read_session_injections(
    store_root: Path,
    session: str,
) -> list[str]:
    """Read all injected slugs for a session, deduplicated."""
    path = _injection_path(store_root, session)
    if not path.exists():
        return []
    slugs: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                slugs.update(data.get("slugs", []))
            except json.JSONDecodeError:
                continue
    return sorted(slugs)


def cleanup_session_file(store_root: Path, session: str) -> None:
    """Delete the session injection tracking file."""
    path = _injection_path(store_root, session)
    if path.exists():
        path.unlink()
