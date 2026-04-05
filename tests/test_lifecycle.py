"""Tests for LifecycleManager — written before implementation (TDD)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from engram.cli import main
from engram.lifecycle import LifecycleManager
from engram.models import (
    Engram,
    EngramState,
    Metrics,
    Triggers,
    TrustLevel,
)
from engram.scanner import EngramScanner
from engram.store import EngramStore


def _make_engram(name: str = "test-engram", **kwargs: object) -> Engram:
    """Helper to create a test engram with sensible defaults."""
    now = datetime.now(tz=UTC)
    defaults: dict[str, object] = {
        "name": name,
        "version": 1,
        "description": f"Test engram: {name}",
        "state": EngramState.DRAFT,
        "created": now,
        "updated": now,
        "trust": TrustLevel.AGENT_CREATED,
        "body": f"## Procedure\nDo the {name} thing.",
    }
    defaults.update(kwargs)
    return Engram(**defaults)  # type: ignore[arg-type]


def _store_with_engram(tmp_store: Path, **kwargs: object) -> tuple[EngramStore, Engram]:
    """Create a store and write a single engram to it."""
    store = EngramStore(tmp_store)
    engram = _make_engram(**kwargs)
    store.write(engram)
    return store, engram


# ---------------------------------------------------------------------------
# Transition detection (check_transitions)
# ---------------------------------------------------------------------------


class TestCheckTransitions:
    def test_draft_to_candidate_when_signals_gte_3_and_score_gte_05(
        self, tmp_store: Path
    ) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DRAFT,
            metrics=Metrics(success_count=1, relevant_count=2, quality_score=0.5),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert len(proposals) == 1
        assert proposals[0].target_state == EngramState.CANDIDATE

    def test_draft_stays_draft_when_signals_too_low(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DRAFT,
            metrics=Metrics(success_count=1, relevant_count=0, quality_score=0.5),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert proposals == []

    def test_draft_stays_draft_when_score_too_low(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DRAFT,
            metrics=Metrics(usage_count=5, quality_score=0.4),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert proposals == []

    def test_candidate_to_stable_when_criteria_met(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.CANDIDATE,
            metrics=Metrics(usage_count=10, quality_score=0.7, streak=5),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert len(proposals) == 1
        assert proposals[0].target_state == EngramState.STABLE

    def test_candidate_stays_when_streak_too_low(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.CANDIDATE,
            metrics=Metrics(usage_count=10, quality_score=0.7, streak=4),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert proposals == []

    def test_stable_to_deprecated_when_score_below_03(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.STABLE,
            metrics=Metrics(usage_count=20, quality_score=0.29),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert len(proposals) == 1
        assert proposals[0].target_state == EngramState.DEPRECATED

    def test_deprecated_to_archived_after_90_days_no_usage(
        self, tmp_store: Path
    ) -> None:
        old_date = datetime.now(tz=UTC) - timedelta(days=91)
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DEPRECATED,
            updated=old_date,
            metrics=Metrics(usage_count=0, quality_score=0.1),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert len(proposals) == 1
        assert proposals[0].target_state == EngramState.ARCHIVED

    def test_deprecated_not_archived_if_recent(self, tmp_store: Path) -> None:
        recent_date = datetime.now(tz=UTC) - timedelta(days=30)
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DEPRECATED,
            updated=recent_date,
            metrics=Metrics(usage_count=0, quality_score=0.1),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert proposals == []

    def test_deprecated_not_archived_if_still_used(self, tmp_store: Path) -> None:
        old_date = datetime.now(tz=UTC) - timedelta(days=91)
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DEPRECATED,
            updated=old_date,
            metrics=Metrics(usage_count=5, quality_score=0.1),
        )
        lm = LifecycleManager(store)
        proposals = lm.check_transitions()
        assert proposals == []


# ---------------------------------------------------------------------------
# State transitions (apply_transition)
# ---------------------------------------------------------------------------


class TestApplyTransition:
    def test_draft_to_candidate(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DRAFT)
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.CANDIDATE, "met criteria"
        )
        assert result.state == EngramState.CANDIDATE
        # Verify persisted
        loaded = store.read("test-engram")
        assert loaded.state == EngramState.CANDIDATE

    def test_candidate_to_stable(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.CANDIDATE)
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.STABLE, "met criteria"
        )
        assert result.state == EngramState.STABLE

    def test_stable_to_deprecated(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.STABLE)
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.DEPRECATED, "quality degraded"
        )
        assert result.state == EngramState.DEPRECATED

    def test_deprecated_to_archived(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DEPRECATED)
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.ARCHIVED, "stale"
        )
        assert result.state == EngramState.ARCHIVED

    def test_deprecated_to_candidate_repromotion(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DEPRECATED)
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.CANDIDATE, "re-promoted after update"
        )
        assert result.state == EngramState.CANDIDATE

    def test_any_state_to_draft_demote(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.STABLE)
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.DRAFT, "needs rework"
        )
        assert result.state == EngramState.DRAFT

    def test_illegal_draft_to_stable_raises(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DRAFT)
        lm = LifecycleManager(store)
        with pytest.raises(ValueError, match="Illegal"):
            lm.apply_transition("test-engram", EngramState.STABLE, "skip")

    def test_illegal_draft_to_archived_raises(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DRAFT)
        lm = LifecycleManager(store)
        with pytest.raises(ValueError, match="Illegal"):
            lm.apply_transition("test-engram", EngramState.ARCHIVED, "skip")

    def test_illegal_archived_to_stable_raises(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.ARCHIVED)
        lm = LifecycleManager(store)
        with pytest.raises(ValueError, match="Illegal"):
            lm.apply_transition("test-engram", EngramState.STABLE, "nope")

    def test_illegal_candidate_to_archived_raises(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.CANDIDATE)
        lm = LifecycleManager(store)
        with pytest.raises(ValueError, match="Illegal"):
            lm.apply_transition("test-engram", EngramState.ARCHIVED, "skip")

    def test_same_state_raises(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DRAFT)
        lm = LifecycleManager(store)
        with pytest.raises(ValueError, match="same state"):
            lm.apply_transition("test-engram", EngramState.DRAFT, "no-op")

    def test_creates_version_snapshot(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store, state=EngramState.DRAFT)
        lm = LifecycleManager(store)
        lm.apply_transition("test-engram", EngramState.CANDIDATE, "promote")
        versions = store.list_versions("test-engram")
        assert 1 in versions

    def test_updates_timestamp(self, tmp_store: Path) -> None:
        old_time = datetime.now(tz=UTC) - timedelta(hours=1)
        store, _ = _store_with_engram(
            tmp_store, state=EngramState.DRAFT, updated=old_time
        )
        lm = LifecycleManager(store)
        result = lm.apply_transition(
            "test-engram", EngramState.CANDIDATE, "promote"
        )
        assert result.updated > old_time

    def test_promotion_blocked_by_critical_scanner_findings(
        self, tmp_store: Path
    ) -> None:
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DRAFT,
            body="Use Bash to run rm -rf /",
        )
        scanner = EngramScanner()
        lm = LifecycleManager(store, scanner=scanner)
        with pytest.raises(ValueError, match="[Ss]canner|[Bb]lock"):
            lm.apply_transition("test-engram", EngramState.CANDIDATE, "promote")

    def test_promotion_without_scanner_succeeds(self, tmp_store: Path) -> None:
        """When no scanner is provided, promotions should not be blocked."""
        store, _ = _store_with_engram(tmp_store, state=EngramState.DRAFT)
        lm = LifecycleManager(store, scanner=None)
        result = lm.apply_transition(
            "test-engram", EngramState.CANDIDATE, "promote"
        )
        assert result.state == EngramState.CANDIDATE


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestCheckDuplicates:
    def test_tag_overlap_gte_60_percent(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        # Tags: python, testing, fixtures, pytest (4 tags)
        existing = _make_engram(
            "existing",
            triggers=Triggers(tags=["python", "testing", "fixtures", "pytest"]),
        )
        store.write(existing)

        # New: python, testing, fixtures, mock
        # Intersection: {python, testing, fixtures} = 3
        # Union: {python, testing, fixtures, pytest, mock} = 5
        # Jaccard = 3/5 = 0.6 >= 0.6
        new_engram = _make_engram(
            "new-engram",
            triggers=Triggers(tags=["python", "testing", "fixtures", "mock"]),
        )

        lm = LifecycleManager(store)
        dupes = lm.check_duplicates(new_engram)
        tag_dupes = [d for d in dupes if d.similarity_type == "tag_overlap"]
        assert len(tag_dupes) >= 1
        assert tag_dupes[0].slug == "existing"

    def test_tag_overlap_below_threshold_not_flagged(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        existing = _make_engram(
            "existing",
            triggers=Triggers(tags=["python", "testing", "fixtures", "pytest", "db"]),
        )
        store.write(existing)

        new_engram = _make_engram(
            "new-engram",
            triggers=Triggers(tags=["java", "spring", "db"]),
        )

        lm = LifecycleManager(store)
        dupes = lm.check_duplicates(new_engram)
        tag_dupes = [d for d in dupes if d.similarity_type == "tag_overlap"]
        # Jaccard: |{db}| / |{python,testing,fixtures,pytest,db,java,spring}| = 1/7 ~= 0.14
        assert tag_dupes == []

    def test_tag_overlap_exact_threshold(self, tmp_store: Path) -> None:
        """Jaccard >= 0.6 should be flagged."""
        store = EngramStore(tmp_store)
        # Tags: a, b, c, d, e  (5 tags)
        existing = _make_engram(
            "existing",
            triggers=Triggers(tags=["a", "b", "c", "d", "e"]),
        )
        store.write(existing)

        # New: a, b, c, d, f  -> intersection={a,b,c,d}=4, union={a,b,c,d,e,f}=6
        # Jaccard = 4/6 = 0.667 >= 0.6
        new_engram = _make_engram(
            "new-engram",
            triggers=Triggers(tags=["a", "b", "c", "d", "f"]),
        )

        lm = LifecycleManager(store)
        dupes = lm.check_duplicates(new_engram)
        tag_dupes = [d for d in dupes if d.similarity_type == "tag_overlap"]
        assert len(tag_dupes) == 1
        assert tag_dupes[0].similarity_score == pytest.approx(4 / 6, abs=0.01)

    def test_description_similarity_gte_07(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        existing = _make_engram(
            "existing",
            description="run database migrations before deploying the application",
        )
        store.write(existing)

        new_engram = _make_engram(
            "new-engram",
            description="run database migrations before deploying the service",
        )
        # Words existing: {run, database, migrations, before, deploying, the, application}
        # Words new:      {run, database, migrations, before, deploying, the, service}
        # Intersection: {run, database, migrations, before, deploying, the} = 6
        # Union: {run, database, migrations, before, deploying, the, application, service} = 8
        # Jaccard = 6/8 = 0.75 >= 0.7

        lm = LifecycleManager(store)
        dupes = lm.check_duplicates(new_engram)
        desc_dupes = [d for d in dupes if d.similarity_type == "description_similarity"]
        assert len(desc_dupes) >= 1
        assert desc_dupes[0].slug == "existing"

    def test_does_not_compare_to_self(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        existing = _make_engram(
            "existing",
            triggers=Triggers(tags=["a", "b", "c"]),
            description="test engram existing",
        )
        store.write(existing)

        lm = LifecycleManager(store)
        # Check duplicates for itself
        dupes = lm.check_duplicates(existing)
        assert dupes == []

    def test_empty_tags_no_overlap(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        existing = _make_engram("existing", triggers=Triggers(tags=[]))
        store.write(existing)

        new_engram = _make_engram("new-engram", triggers=Triggers(tags=[]))
        lm = LifecycleManager(store)
        dupes = lm.check_duplicates(new_engram)
        tag_dupes = [d for d in dupes if d.similarity_type == "tag_overlap"]
        assert tag_dupes == []


# ---------------------------------------------------------------------------
# Garbage collection
# ---------------------------------------------------------------------------


class TestRunGC:
    def test_archives_stale_deprecated_engrams(self, tmp_store: Path) -> None:
        old_date = datetime.now(tz=UTC) - timedelta(days=91)
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DEPRECATED,
            updated=old_date,
            metrics=Metrics(usage_count=0, quality_score=0.1),
        )
        lm = LifecycleManager(store)
        report = lm.run_gc()
        assert "test-engram" in report.archived
        # Should be moved to archive
        assert (tmp_store / "archive" / "test-engram.md").exists()
        assert not (tmp_store / "engram" / "test-engram.md").exists()

    def test_does_not_archive_recent_deprecated(self, tmp_store: Path) -> None:
        recent = datetime.now(tz=UTC) - timedelta(days=30)
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DEPRECATED,
            updated=recent,
            metrics=Metrics(usage_count=0, quality_score=0.1),
        )
        lm = LifecycleManager(store)
        report = lm.run_gc()
        assert report.archived == []

    def test_cleans_orphaned_metrics_files(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        # Write an engram
        store.write(_make_engram("real-engram"))
        # Create an orphaned metrics file (no matching engram)
        orphan_metrics = tmp_store / "metrics" / "orphan-engram.jsonl"
        orphan_metrics.write_text('{"ts":"2024-01-01T00:00:00Z","event":"used","session":"x"}\n')
        # Also create a non-orphan metrics file
        real_metrics = tmp_store / "metrics" / "real-engram.jsonl"
        real_metrics.write_text('{"ts":"2024-01-01T00:00:00Z","event":"used","session":"x"}\n')

        lm = LifecycleManager(store)
        report = lm.run_gc()
        assert "orphan-engram" in report.orphan_metrics_cleaned
        assert "real-engram" not in report.orphan_metrics_cleaned
        assert not orphan_metrics.exists()
        assert real_metrics.exists()

    def test_cleans_orphaned_version_dirs(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram("real-engram"))
        # Create an orphaned versions directory
        orphan_dir = tmp_store / "versions" / "orphan-engram"
        orphan_dir.mkdir(parents=True)
        (orphan_dir / "v1.md").write_text("old content")
        # Also create a real versions directory
        real_dir = tmp_store / "versions" / "real-engram"
        real_dir.mkdir(parents=True)
        (real_dir / "v1.md").write_text("old content")

        lm = LifecycleManager(store)
        report = lm.run_gc()
        assert "orphan-engram" in report.orphan_versions_cleaned
        assert "real-engram" not in report.orphan_versions_cleaned
        assert not orphan_dir.exists()
        assert real_dir.exists()


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_restores_old_content(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        # Write v1
        v1 = _make_engram(description="original", body="## Original\nOld content.")
        store.write(v1)
        store.save_version("test-engram", 1)
        # Write v2
        v2 = _make_engram(
            version=2, description="updated", body="## Updated\nNew content."
        )
        store.write(v2)
        store.save_version("test-engram", 2)

        lm = LifecycleManager(store)
        result = lm.rollback("test-engram", 1)
        assert result.body == "## Original\nOld content."
        assert result.description == "original"

    def test_rollback_increments_version(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        v1 = _make_engram(description="original")
        store.write(v1)
        store.save_version("test-engram", 1)
        v2 = _make_engram(version=2, description="updated")
        store.write(v2)
        store.save_version("test-engram", 2)

        lm = LifecycleManager(store)
        result = lm.rollback("test-engram", 1)
        assert result.version == 3  # current was v2, so rolled back = v3

    def test_rollback_resets_quality_score(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        v1 = _make_engram(metrics=Metrics(quality_score=0.9))
        store.write(v1)
        store.save_version("test-engram", 1)
        v2 = _make_engram(version=2, metrics=Metrics(quality_score=0.2))
        store.write(v2)

        lm = LifecycleManager(store)
        result = lm.rollback("test-engram", 1)
        assert result.metrics.quality_score == 0.5

    def test_rollback_saves_current_as_version(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        v1 = _make_engram(description="original")
        store.write(v1)
        store.save_version("test-engram", 1)
        v2 = _make_engram(version=2, description="updated")
        store.write(v2)

        lm = LifecycleManager(store)
        lm.rollback("test-engram", 1)
        # Version 2 should be saved as a snapshot
        versions = store.list_versions("test-engram")
        assert 2 in versions

    def test_rollback_nonexistent_version_raises(self, tmp_store: Path) -> None:
        store, _ = _store_with_engram(tmp_store)
        lm = LifecycleManager(store)
        with pytest.raises(FileNotFoundError):
            lm.rollback("test-engram", 99)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _make_cli_store(store_path: Path) -> EngramStore:
    """Create a store with subdirs for CLI testing."""
    for subdir in ("engram", "archive", "metrics", "versions"):
        (store_path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(store_path)


class TestCLIPromote:
    def test_promote_draft_to_candidate(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(_make_engram(state=EngramState.DRAFT))
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "promote", "test-engram"]
        )
        assert result.exit_code == 0
        assert "candidate" in result.output.lower()

    def test_promote_candidate_to_stable(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(_make_engram(state=EngramState.CANDIDATE))
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "promote", "test-engram"]
        )
        assert result.exit_code == 0
        assert "stable" in result.output.lower()

    def test_promote_stable_fails(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(_make_engram(state=EngramState.STABLE))
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "promote", "test-engram"]
        )
        assert result.exit_code != 0

    def test_promote_not_found(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _make_cli_store(store_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "promote", "nonexistent"]
        )
        assert result.exit_code != 0


class TestCLIDeprecate:
    def test_deprecate_stable(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(_make_engram(state=EngramState.STABLE))
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "deprecate", "test-engram"]
        )
        assert result.exit_code == 0
        assert "deprecated" in result.output.lower()


class TestCLIArchive:
    def test_archive_deprecated(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(_make_engram(state=EngramState.DEPRECATED))
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "archive", "test-engram"]
        )
        assert result.exit_code == 0
        assert "archived" in result.output.lower()


class TestCLIDemote:
    def test_demote_to_draft(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(_make_engram(state=EngramState.STABLE))
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "demote", "test-engram"]
        )
        assert result.exit_code == 0
        assert "draft" in result.output.lower()


class TestCLIRollback:
    def test_rollback_to_version(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        v1 = _make_engram(description="original")
        store.write(v1)
        store.save_version("test-engram", 1)
        v2 = _make_engram(version=2, description="updated")
        store.write(v2)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "rollback", "test-engram", "1"]
        )
        assert result.exit_code == 0
        assert "rollback" in result.output.lower() or "v3" in result.output.lower()


class TestCLIGC:
    def test_gc_runs(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _make_cli_store(store_path)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "gc"])
        assert result.exit_code == 0


class TestCLIDedup:
    def test_dedup_shows_candidates(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        store = _make_cli_store(store_path)
        store.write(
            _make_engram(
                "existing",
                triggers=Triggers(tags=["a", "b", "c", "d", "e"]),
                description="some unique description for existing",
            )
        )
        store.write(
            _make_engram(
                "new-engram",
                triggers=Triggers(tags=["a", "b", "c", "d", "f"]),
                description="some unique description for new",
            )
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "dedup", "new-engram"]
        )
        assert result.exit_code == 0
        assert "existing" in result.output

    def test_dedup_not_found(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _make_cli_store(store_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "dedup", "nonexistent"]
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# GC: pinned engrams and underscore-prefixed metrics
# ---------------------------------------------------------------------------


class TestGCPinnedAndUnderscoreMetrics:
    def test_gc_skips_pinned_deprecated(self, tmp_store: Path) -> None:
        """Pinned deprecated engrams older than 90 days are NOT archived by GC."""
        old_date = datetime.now(tz=UTC) - timedelta(days=91)
        store, _ = _store_with_engram(
            tmp_store,
            state=EngramState.DEPRECATED,
            updated=old_date,
            metrics=Metrics(usage_count=0, quality_score=0.1),
            pinned=True,
        )
        lm = LifecycleManager(store)
        report = lm.run_gc()
        assert report.archived == []
        # Engram should still exist in the active store
        engram = store.read("test-engram")
        assert engram.state == EngramState.DEPRECATED

    def test_gc_skips_underscore_metrics(self, tmp_store: Path) -> None:
        """Metrics files prefixed with _ (e.g., _inj_session.jsonl) are not cleaned."""
        store = EngramStore(tmp_store)
        # No engrams in store, so any non-underscore metrics file is orphaned
        metrics_dir = tmp_store / "metrics"
        metrics_dir.mkdir(exist_ok=True)

        # Create an underscore-prefixed file (injection tracking)
        underscore_file = metrics_dir / "_inj_sess-1.jsonl"
        underscore_file.write_text('{"ts":"2024-01-01","slugs":["a"]}\n')

        # Create a regular orphan metrics file for comparison
        orphan_file = metrics_dir / "orphan-engram.jsonl"
        orphan_file.write_text('{"ts":"2024-01-01","event":"used","session":"x"}\n')

        lm = LifecycleManager(store)
        report = lm.run_gc()

        # The underscore file should NOT be cleaned
        assert underscore_file.exists()
        assert "_inj_sess-1" not in report.orphan_metrics_cleaned

        # The regular orphan should be cleaned
        assert "orphan-engram" in report.orphan_metrics_cleaned
        assert not orphan_file.exists()
