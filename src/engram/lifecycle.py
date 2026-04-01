"""Engram lifecycle manager — state transitions, dedup, GC, rollback."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

from engram.models import (
    DedupCandidate,
    Engram,
    EngramState,
    GCReport,
    TransitionProposal,
)
from engram.scanner import EngramScanner
from engram.store import EngramStore

# Legal state transitions: {(from_state, to_state)}
# "any -> draft" is handled separately below.
_LEGAL_TRANSITIONS: frozenset[tuple[EngramState, EngramState]] = frozenset({
    (EngramState.DRAFT, EngramState.CANDIDATE),
    (EngramState.CANDIDATE, EngramState.STABLE),
    (EngramState.STABLE, EngramState.DEPRECATED),
    (EngramState.DEPRECATED, EngramState.ARCHIVED),
    (EngramState.DEPRECATED, EngramState.CANDIDATE),  # re-promotion
})

# Transitions that count as "promotions" and require scanner check
_PROMOTION_TRANSITIONS: frozenset[tuple[EngramState, EngramState]] = frozenset({
    (EngramState.DRAFT, EngramState.CANDIDATE),
    (EngramState.CANDIDATE, EngramState.STABLE),
})


class LifecycleManager:
    """Manages engram lifecycle: transitions, deduplication, GC, and rollback."""

    def __init__(
        self,
        store: EngramStore,
        scanner: EngramScanner | None = None,
    ) -> None:
        self._store = store
        self._scanner = scanner

    def check_transitions(self) -> list[TransitionProposal]:
        """Scan all engrams and propose state transitions based on metrics."""
        proposals: list[TransitionProposal] = []
        now = datetime.now(tz=UTC)

        for slug in self._store.list():
            engram = self._store.read(slug)
            m = engram.metrics

            if engram.state == EngramState.DRAFT:
                if m.usage_count >= 3 and m.quality_score >= 0.5:
                    proposals.append(TransitionProposal(
                        slug=slug,
                        current_state=engram.state,
                        target_state=EngramState.CANDIDATE,
                        reason=f"usage_count={m.usage_count}, quality_score={m.quality_score}",
                    ))

            elif engram.state == EngramState.CANDIDATE:
                if m.usage_count >= 10 and m.quality_score >= 0.7 and m.streak >= 5:
                    proposals.append(TransitionProposal(
                        slug=slug,
                        current_state=engram.state,
                        target_state=EngramState.STABLE,
                        reason=(
                            f"usage_count={m.usage_count}, "
                            f"quality_score={m.quality_score}, "
                            f"streak={m.streak}"
                        ),
                    ))

            elif engram.state == EngramState.STABLE:
                if m.quality_score < 0.3:
                    proposals.append(TransitionProposal(
                        slug=slug,
                        current_state=engram.state,
                        target_state=EngramState.DEPRECATED,
                        reason=f"quality_score={m.quality_score} < 0.3",
                    ))

            elif engram.state == EngramState.DEPRECATED:
                days_since_update = (now - engram.updated).days
                if days_since_update >= 90 and m.usage_count == 0:
                    proposals.append(TransitionProposal(
                        slug=slug,
                        current_state=engram.state,
                        target_state=EngramState.ARCHIVED,
                        reason=(
                            f"deprecated for {days_since_update} days "
                            f"with no usage"
                        ),
                    ))

        return proposals

    def apply_transition(
        self,
        slug: str,
        target_state: EngramState,
        reason: str,
    ) -> Engram:
        """Transition an engram to a new state with validation and versioning."""
        engram = self._store.read(slug)
        current_state = engram.state

        # Reject same-state transitions
        if current_state == target_state:
            raise ValueError(
                f"Cannot transition to same state: "
                f"{current_state.value} -> {target_state.value}"
            )

        # Check if transition is legal
        is_demote_to_draft = target_state == EngramState.DRAFT
        is_legal = (current_state, target_state) in _LEGAL_TRANSITIONS

        if not is_demote_to_draft and not is_legal:
            raise ValueError(
                f"Illegal transition: "
                f"{current_state.value} -> {target_state.value}"
            )

        # For archived, only allow transition to draft
        if current_state == EngramState.ARCHIVED and target_state != EngramState.DRAFT:
            raise ValueError(
                f"Illegal transition: archived engrams can only be demoted to draft, "
                f"not {target_state.value}"
            )

        # For promotions, run scanner if available
        if (current_state, target_state) in _PROMOTION_TRANSITIONS and self._scanner:
            verdict = self._scanner.scan(engram)
            if verdict.action == "block":
                raise ValueError(
                    f"Scanner blocked promotion of '{slug}': "
                    f"{len(verdict.results)} finding(s)"
                )

        # Save current version as snapshot before transition
        self._store.save_version(slug, engram.version)

        # Apply transition
        engram.state = target_state
        engram.updated = datetime.now(tz=UTC)
        self._store.write(engram)

        return engram

    def check_duplicates(self, engram: Engram) -> list[DedupCandidate]:
        """Find potential duplicates for a proposed engram."""
        candidates: list[DedupCandidate] = []
        new_tags = set(engram.triggers.tags)
        new_words = _tokenize(engram.description)

        for slug in self._store.list():
            # Don't compare to self
            if slug == engram.name:
                continue

            existing = self._store.read(slug)

            # Tag overlap (Jaccard similarity)
            existing_tags = set(existing.triggers.tags)
            if new_tags and existing_tags:
                intersection = new_tags & existing_tags
                union = new_tags | existing_tags
                jaccard = len(intersection) / len(union)
                if jaccard >= 0.6:
                    candidates.append(DedupCandidate(
                        slug=slug,
                        similarity_type="tag_overlap",
                        similarity_score=round(jaccard, 4),
                        description=existing.description,
                    ))

            # Description similarity (Jaccard on word tokens)
            existing_words = _tokenize(existing.description)
            if new_words and existing_words:
                word_intersection = new_words & existing_words
                word_union = new_words | existing_words
                word_jaccard = len(word_intersection) / len(word_union)
                if word_jaccard >= 0.7:
                    candidates.append(DedupCandidate(
                        slug=slug,
                        similarity_type="description_similarity",
                        similarity_score=round(word_jaccard, 4),
                        description=existing.description,
                    ))

        return candidates

    def run_gc(self) -> GCReport:
        """Garbage collection: archive stale, clean orphans."""
        report = GCReport()
        now = datetime.now(tz=UTC)
        known_slugs = set(self._store.list())

        # 1. Archive stale deprecated engrams
        for slug in list(known_slugs):
            engram = self._store.read(slug)
            if engram.state == EngramState.DEPRECATED:
                days_since_update = (now - engram.updated).days
                if days_since_update >= 90 and engram.metrics.usage_count == 0:
                    self._store.move_to_archive(slug)
                    report.archived.append(slug)
                    known_slugs.discard(slug)

        # 2. Clean orphaned metrics files
        metrics_dir = self._store.root / "metrics"
        if metrics_dir.exists():
            for metrics_file in sorted(metrics_dir.glob("*.jsonl")):
                slug = metrics_file.stem
                if slug not in known_slugs:
                    metrics_file.unlink()
                    report.orphan_metrics_cleaned.append(slug)

        # 3. Clean orphaned version dirs
        versions_dir = self._store.root / "versions"
        if versions_dir.exists():
            for version_dir in sorted(versions_dir.iterdir()):
                if version_dir.is_dir():
                    slug = version_dir.name
                    if slug not in known_slugs:
                        shutil.rmtree(version_dir)
                        report.orphan_versions_cleaned.append(slug)

        return report

    def rollback(self, slug: str, version: int) -> Engram:
        """Rollback an engram to a previous version."""
        # Read the target version (raises FileNotFoundError if not found)
        old_version = self._store.get_version(slug, version)

        # Read current engram
        current = self._store.read(slug)

        # Save current as a version snapshot
        self._store.save_version(slug, current.version)

        # Write old version content as new current with incremented version
        old_version.version = current.version + 1
        old_version.metrics.quality_score = 0.5
        old_version.updated = datetime.now(tz=UTC)
        self._store.write(old_version)

        return old_version


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase word tokens."""
    return set(text.lower().split())
