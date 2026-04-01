"""Tests for EngramEvaluator and hooks — written before implementation (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from engram.cli import main
from engram.models import Engram, EngramState, MetricEvent, Metrics, TrustLevel
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


def _make_event(
    event: str = "used",
    ts: datetime | None = None,
    session: str = "sess-1",
    **kwargs: object,
) -> MetricEvent:
    """Helper to create a MetricEvent with defaults."""
    return MetricEvent(
        ts=ts or datetime.now(tz=UTC),
        event=event,  # type: ignore[arg-type]
        session=session,
        **kwargs,  # type: ignore[arg-type]
    )


def _get_store(tmp_store: Path) -> EngramStore:
    return EngramStore(tmp_store)


class TestComputeQualityScore:
    """Test the quality score computation algorithm."""

    def test_empty_events_returns_neutral(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        score = evaluator.compute_quality_score([])
        assert score == 0.5

    def test_only_used_events_returns_neutral(self, tmp_store: Path) -> None:
        """Usage events alone don't change the score."""
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("used") for _ in range(5)]
        score = evaluator.compute_quality_score(events)
        assert score == 0.5

    def test_successes_trend_toward_one(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("used")]
        events += [_make_event("success") for _ in range(10)]
        score = evaluator.compute_quality_score(events)
        assert score > 0.9
        assert score <= 1.0

    def test_overrides_trend_toward_zero(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("used")]
        events += [_make_event("override") for _ in range(10)]
        score = evaluator.compute_quality_score(events)
        assert score < 0.01
        assert score >= 0.0

    def test_feedback_up_increases_score(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("used")]
        events.append(_make_event("feedback", rating="up"))
        score = evaluator.compute_quality_score(events)
        assert score == 0.75  # 0.5 + 0.5*(1-0.5) = 0.75

    def test_feedback_down_decreases_score(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("used")]
        events.append(_make_event("feedback", rating="down"))
        score = evaluator.compute_quality_score(events)
        assert score == 0.25  # 0.5 - 0.5*0.5 = 0.25

    def test_rolling_window_limits_to_30(self, tmp_store: Path) -> None:
        """Only the last 30 events matter."""
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        # 20 overrides (will be outside window if we add 30 more)
        old_events = [_make_event("override") for _ in range(20)]
        # 1 used + 29 successes (these are the last 30)
        recent_events = [_make_event("used")]
        recent_events += [_make_event("success") for _ in range(29)]
        all_events = old_events + recent_events
        assert len(all_events) == 50

        score = evaluator.compute_quality_score(all_events)
        # The 20 old overrides should be excluded from the window.
        # Window = last 30: 1 used + 29 successes. Should be high.
        assert score > 0.9

    def test_staleness_decay_after_30_days(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        old_ts = datetime.now(tz=UTC) - timedelta(days=90)
        events = [
            _make_event("used", ts=old_ts),
            _make_event("success", ts=old_ts),
        ]
        score = evaluator.compute_quality_score(events)
        # 60 days stale (90 - 30), so 2 months_stale
        # Base score after 1 success: 0.5 + 0.3*(1-0.5) = 0.65
        # Decay: 0.65 * max(0.1, 1.0 - 0.1*2) = 0.65 * 0.8 = 0.52
        assert score == 0.52

    def test_staleness_floor_at_ten_percent(self, tmp_store: Path) -> None:
        """Staleness decay should not reduce multiplier below 0.1."""
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        # 400 days ago -> 370 days stale -> ~12.3 months_stale
        old_ts = datetime.now(tz=UTC) - timedelta(days=400)
        events = [
            _make_event("used", ts=old_ts),
            _make_event("success", ts=old_ts),
        ]
        score = evaluator.compute_quality_score(events)
        # Base: 0.65, multiplier: max(0.1, 1.0 - 0.1*12.3) = 0.1
        # 0.65 * 0.1 = 0.065
        assert score == 0.065

    def test_score_clamped_to_unit_interval(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        # Many successes + feedback up should not exceed 1.0
        events = [_make_event("used")]
        events += [_make_event("success") for _ in range(20)]
        events += [_make_event("feedback", rating="up") for _ in range(5)]
        score = evaluator.compute_quality_score(events)
        assert 0.0 <= score <= 1.0

    def test_score_rounded_to_three_decimal_places(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("used"), _make_event("success")]
        score = evaluator.compute_quality_score(events)
        # Check that it has at most 3 decimal places
        assert score == round(score, 3)

    def test_no_usage_events_returns_neutral(self, tmp_store: Path) -> None:
        """If there are events but no 'used' events, score is neutral."""
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = [_make_event("success"), _make_event("success")]
        score = evaluator.compute_quality_score(events)
        assert score == 0.5


class TestJSONLReadWrite:
    """Test JSONL sidecar read/write operations."""

    def test_append_and_read_events(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        ev1 = _make_event("used", session="s1")
        ev2 = _make_event("success", session="s1")

        evaluator.append_event("test-engram", ev1)
        evaluator.append_event("test-engram", ev2)

        events = evaluator.read_events("test-engram")
        assert len(events) == 2
        assert events[0].event == "used"
        assert events[1].event == "success"

    def test_read_events_empty_file(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        events = evaluator.read_events("nonexistent")
        assert events == []

    def test_append_creates_metrics_dir(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        ev = _make_event("used")
        evaluator.append_event("test-engram", ev)
        assert (tmp_store / "metrics" / "test-engram.jsonl").exists()

    def test_events_survive_roundtrip_serialization(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        evaluator = EngramEvaluator(store)
        ts = datetime(2026, 3, 28, 14, 20, 0, tzinfo=UTC)
        ev = MetricEvent(
            ts=ts, event="feedback", session="abc", rating="up",
            context="test ctx", detail="test detail",
        )
        evaluator.append_event("test-engram", ev)
        loaded = evaluator.read_events("test-engram")
        assert len(loaded) == 1
        assert loaded[0].ts == ts
        assert loaded[0].event == "feedback"
        assert loaded[0].rating == "up"
        assert loaded[0].context == "test ctx"
        assert loaded[0].detail == "test detail"


class TestUpdateEngramScore:
    """Test score update flow: read events -> compute -> write to frontmatter."""

    def test_update_engram_score(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        store.write(_make_engram("test-engram"))
        evaluator = EngramEvaluator(store)

        evaluator.append_event("test-engram", _make_event("used"))
        evaluator.append_event("test-engram", _make_event("success"))
        evaluator.append_event("test-engram", _make_event("success"))

        new_score = evaluator.update_engram_score("test-engram")
        assert new_score > 0.5

        # Verify score persisted in engram frontmatter
        engram = store.read("test-engram")
        assert engram.metrics.quality_score == new_score

    def test_update_all_scores(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store = _get_store(tmp_store)
        store.write(_make_engram("engram-a"))
        store.write(_make_engram("engram-b"))
        evaluator = EngramEvaluator(store)

        evaluator.append_event("engram-a", _make_event("used"))
        evaluator.append_event("engram-a", _make_event("success"))
        evaluator.append_event("engram-b", _make_event("used"))
        evaluator.append_event("engram-b", _make_event("override"))

        scores = evaluator.update_all_scores()
        assert "engram-a" in scores
        assert "engram-b" in scores
        assert scores["engram-a"] > 0.5
        assert scores["engram-b"] < 0.5


class TestHooks:
    """Test signal capture helpers."""

    def test_record_signal(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator
        from engram.hooks import record_signal

        record_signal(tmp_store, "test-engram", "used", "sess-1", context="deploy")
        evaluator = EngramEvaluator(_get_store(tmp_store))
        events = evaluator.read_events("test-engram")
        assert len(events) == 1
        assert events[0].event == "used"
        assert events[0].session == "sess-1"
        assert events[0].context == "deploy"

    def test_record_session_end(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator
        from engram.hooks import record_session_end

        record_session_end(tmp_store, "sess-1", ["engram-a", "engram-b"], "success")
        evaluator = EngramEvaluator(_get_store(tmp_store))

        events_a = evaluator.read_events("engram-a")
        events_b = evaluator.read_events("engram-b")
        assert len(events_a) == 1
        assert events_a[0].event == "success"
        assert len(events_b) == 1
        assert events_b[0].event == "success"

    def test_record_session_end_override(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator
        from engram.hooks import record_session_end

        record_session_end(tmp_store, "sess-1", ["engram-a"], "override")
        evaluator = EngramEvaluator(_get_store(tmp_store))
        events = evaluator.read_events("engram-a")
        assert len(events) == 1
        assert events[0].event == "override"

    def test_record_feedback(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator
        from engram.hooks import record_feedback

        record_feedback(tmp_store, "test-engram", "sess-1", "up")
        evaluator = EngramEvaluator(_get_store(tmp_store))
        events = evaluator.read_events("test-engram")
        assert len(events) == 1
        assert events[0].event == "feedback"
        assert events[0].rating == "up"

    def test_record_feedback_down(self, tmp_store: Path) -> None:
        from engram.evaluator import EngramEvaluator
        from engram.hooks import record_feedback

        record_feedback(tmp_store, "test-engram", "sess-1", "down")
        evaluator = EngramEvaluator(_get_store(tmp_store))
        events = evaluator.read_events("test-engram")
        assert len(events) == 1
        assert events[0].rating == "down"


class TestCLIStats:
    """Test CLI stats command."""

    def test_stats_all(self, tmp_path: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        store = EngramStore(store_path)
        store.write(_make_engram("test-engram", metrics=Metrics(
            usage_count=5, success_count=3, override_count=1,
            quality_score=0.75,
        )))
        evaluator = EngramEvaluator(store)
        evaluator.append_event("test-engram", _make_event("used"))
        evaluator.append_event("test-engram", _make_event("success"))

        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "stats"])
        assert result.exit_code == 0
        assert "test-engram" in result.output
        assert "0.75" in result.output

    def test_stats_single_slug(self, tmp_path: Path) -> None:
        from engram.evaluator import EngramEvaluator

        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        store = EngramStore(store_path)
        store.write(_make_engram("test-engram"))
        evaluator = EngramEvaluator(store)
        evaluator.append_event("test-engram", _make_event("used"))
        evaluator.append_event("test-engram", _make_event("success"))

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "stats", "--slug", "test-engram",
        ])
        assert result.exit_code == 0
        assert "test-engram" in result.output

    def test_stats_nonexistent_slug(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "stats", "--slug", "nonexistent",
        ])
        assert result.exit_code != 0


class TestCLIRate:
    """Test CLI rate command."""

    def test_rate_up(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        store = EngramStore(store_path)
        store.write(_make_engram("test-engram"))

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "rate", "test-engram", "up",
        ])
        assert result.exit_code == 0
        assert "up" in result.output.lower() or "score" in result.output.lower()

        # Verify feedback was recorded
        from engram.evaluator import EngramEvaluator
        evaluator = EngramEvaluator(store)
        events = evaluator.read_events("test-engram")
        assert any(e.event == "feedback" and e.rating == "up" for e in events)

    def test_rate_down(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        store = EngramStore(store_path)
        store.write(_make_engram("test-engram"))

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "rate", "test-engram", "down",
        ])
        assert result.exit_code == 0

    def test_rate_nonexistent_engram(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "rate", "nonexistent", "up",
        ])
        assert result.exit_code != 0

    def test_rate_invalid_rating(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "rate", "test-engram", "neutral",
        ])
        assert result.exit_code != 0


class TestCLISignal:
    """Test CLI signal command."""

    def test_signal_used(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "signal",
            "--event", "used", "--session", "sess-1", "--slug", "test-engram",
        ])
        assert result.exit_code == 0

        from engram.evaluator import EngramEvaluator
        store = EngramStore(store_path)
        evaluator = EngramEvaluator(store)
        events = evaluator.read_events("test-engram")
        assert len(events) == 1
        assert events[0].event == "used"

    def test_signal_requires_slug(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "signal",
            "--event", "used", "--session", "sess-1",
        ])
        assert result.exit_code != 0
