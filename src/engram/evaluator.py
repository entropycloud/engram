"""Engram evaluator — quality score computation and metric event storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from engram.models import MetricEvent
from engram.store import EngramStore


class EngramEvaluator:
    """Computes quality scores from metric events stored in JSONL sidecars."""

    def __init__(self, store: EngramStore) -> None:
        self._store = store
        self._metrics_dir = store.root / "metrics"

    def _sidecar_path(self, slug: str) -> Path:
        return self._metrics_dir / f"{slug}.jsonl"

    def append_event(self, slug: str, event: MetricEvent) -> None:
        """Append a metric event to the JSONL sidecar for an engram."""
        self._metrics_dir.mkdir(parents=True, exist_ok=True)
        path = self._sidecar_path(slug)
        line = event.model_dump_json() + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    def read_events(self, slug: str) -> list[MetricEvent]:
        """Read all metric events from the JSONL sidecar."""
        path = self._sidecar_path(slug)
        if not path.exists():
            return []
        events: list[MetricEvent] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                events.append(MetricEvent.model_validate(data))
        return events

    def compute_quality_score(
        self, events: list[MetricEvent], *, pinned: bool = False,
    ) -> float:
        """Compute quality score from recent metric events.

        Returns a float in [0.0, 1.0].

        Algorithm:
        - Rolling window of last 30 events
        - Start at 0.5 (neutral prior)
        - success: score += 0.3 * (1 - score)
        - override: score -= 0.5 * score
        - feedback up: score += 0.5 * (1 - score)
        - feedback down: score -= 0.5 * score
        - Staleness decay if last usage > 30 days (skipped for pinned engrams)
        - Clamp to [0.0, 1.0], round to 3 decimal places
        """
        recent = events[-30:]

        if not recent:
            return 0.5

        usage_events = [e for e in recent if e.event == "used"]
        if not usage_events:
            return 0.5

        score = 0.5
        for event in recent:
            if event.event == "success":
                score += 0.3 * (1 - score)
            elif event.event == "override":
                score -= 0.5 * score
            elif event.event == "feedback":
                if event.rating == "up":
                    score += 0.5 * (1 - score)
                else:
                    score -= 0.5 * score

        # Staleness decay (skipped for pinned engrams)
        if not pinned and usage_events:
            now = datetime.now(tz=UTC)
            days_since_use = (now - usage_events[-1].ts).days
            if days_since_use > 30:
                months_stale = (days_since_use - 30) / 30
                score *= max(0.1, 1.0 - 0.1 * months_stale)

        # Clamp and round
        score = max(0.0, min(1.0, score))
        return round(score, 3)

    def update_engram_score(self, slug: str) -> float:
        """Read events, compute score, update engram frontmatter. Returns new score."""
        events = self.read_events(slug)
        engram = self._store.read(slug)
        score = self.compute_quality_score(events, pinned=engram.pinned)
        engram.metrics.quality_score = score
        engram.metrics.last_evaluated = datetime.now(tz=UTC)

        # Update usage/success/override counts from all events (not just window)
        engram.metrics.usage_count = sum(1 for e in events if e.event == "used")
        engram.metrics.success_count = sum(1 for e in events if e.event == "success")
        engram.metrics.override_count = sum(1 for e in events if e.event == "override")
        if events:
            usage_events = [e for e in events if e.event == "used"]
            if usage_events:
                engram.metrics.last_used = usage_events[-1].ts

        self._store.write(engram)
        return score

    def update_all_scores(self) -> dict[str, float]:
        """Update quality scores for all engrams. Returns slug->score mapping."""
        scores: dict[str, float] = {}
        for slug in self._store.list():
            scores[slug] = self.update_engram_score(slug)
        return scores
