"""Tests for Engram Pydantic models — written before implementation (TDD)."""

from datetime import UTC, datetime

import pytest

from engram.models import (
    Engram,
    EngramState,
    IndexEntry,
    Lineage,
    MetricEvent,
    Metrics,
    ScanResult,
    ScanVerdict,
    StoreIndex,
    Triggers,
    TrustLevel,
)


class TestEnums:
    def test_trust_levels(self) -> None:
        assert TrustLevel.SYSTEM == "system"
        assert TrustLevel.VERIFIED == "verified"
        assert TrustLevel.COMMUNITY == "community"
        assert TrustLevel.AGENT_CREATED == "agent-created"

    def test_engram_states(self) -> None:
        assert EngramState.DRAFT == "draft"
        assert EngramState.CANDIDATE == "candidate"
        assert EngramState.STABLE == "stable"
        assert EngramState.DEPRECATED == "deprecated"
        assert EngramState.ARCHIVED == "archived"


class TestTriggers:
    def test_defaults(self) -> None:
        t = Triggers()
        assert t.tags == []
        assert t.patterns == []
        assert t.projects == []
        assert t.files == []

    def test_with_values(self) -> None:
        t = Triggers(
            tags=["deploy", "migration"],
            patterns=["deploy|restart"],
            projects=["/data/dev/myapp/*"],
            files=["**/alembic.ini"],
        )
        assert "deploy" in t.tags
        assert len(t.patterns) == 1


class TestMetrics:
    def test_defaults(self) -> None:
        m = Metrics()
        assert m.usage_count == 0
        assert m.success_count == 0
        assert m.override_count == 0
        assert m.last_used is None
        assert m.last_evaluated is None
        assert m.quality_score == 0.0
        assert m.streak == 0

    def test_with_values(self) -> None:
        now = datetime.now(tz=UTC)
        m = Metrics(usage_count=12, success_count=10, quality_score=0.82, last_used=now)
        assert m.usage_count == 12
        assert m.quality_score == 0.82


class TestLineage:
    def test_defaults(self) -> None:
        ln = Lineage()
        assert ln.parent is None
        assert ln.created_from is None
        assert ln.creation_reason is None


class TestEngram:
    def test_minimal_engram(self) -> None:
        now = datetime.now(tz=UTC)
        e = Engram(
            name="test-engram",
            version=1,
            description="A test engram",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            body="## Procedure\nDo the thing.",
        )
        assert e.name == "test-engram"
        assert e.state == EngramState.DRAFT
        assert e.triggers.tags == []
        assert e.metrics.usage_count == 0

    def test_full_engram(self) -> None:
        now = datetime.now(tz=UTC)
        e = Engram(
            name="run-migrations-before-restart",
            version=3,
            description="Run Alembic migrations before restarting the app service",
            state=EngramState.CANDIDATE,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            triggers=Triggers(
                tags=["deploy", "alembic"],
                patterns=["deploy|restart"],
            ),
            metrics=Metrics(usage_count=12, success_count=10, quality_score=0.82),
            lineage=Lineage(
                created_from="session-abc123",
                creation_reason="discovered migration ordering dependency",
            ),
            allowed_tools=["Bash", "Read", "Edit"],
            body="## Procedure\n1. Run migrations\n2. Restart service",
        )
        assert e.version == 3
        assert e.metrics.quality_score == 0.82
        assert "Bash" in e.allowed_tools

    def test_invalid_name_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValueError):
            Engram(
                name="has spaces in name",
                version=1,
                description="test",
                state=EngramState.DRAFT,
                created=now,
                updated=now,
                trust=TrustLevel.AGENT_CREATED,
                body="body",
            )

    def test_invalid_version_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValueError):
            Engram(
                name="test",
                version=0,
                description="test",
                state=EngramState.DRAFT,
                created=now,
                updated=now,
                trust=TrustLevel.AGENT_CREATED,
                body="body",
            )

    def test_quality_score_clamped(self) -> None:
        with pytest.raises(ValueError):
            Metrics(quality_score=1.5)

    def test_roundtrip_dict(self) -> None:
        now = datetime.now(tz=UTC)
        e = Engram(
            name="test-roundtrip",
            version=1,
            description="Roundtrip test",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            body="body",
        )
        d = e.model_dump()
        e2 = Engram.model_validate(d)
        assert e == e2


class TestMetricEvent:
    def test_usage_event(self) -> None:
        now = datetime.now(tz=UTC)
        ev = MetricEvent(ts=now, event="used", session="abc123", context="deploy task")
        assert ev.event == "used"
        assert ev.context == "deploy task"

    def test_feedback_event(self) -> None:
        now = datetime.now(tz=UTC)
        ev = MetricEvent(ts=now, event="feedback", session="abc123", rating="up")
        assert ev.rating == "up"


class TestIndexEntry:
    def test_from_engram(self) -> None:
        now = datetime.now(tz=UTC)
        e = Engram(
            name="test-index",
            version=2,
            description="Index test",
            state=EngramState.CANDIDATE,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            triggers=Triggers(tags=["test"]),
            metrics=Metrics(quality_score=0.7),
            body="body",
        )
        entry = IndexEntry.from_engram(e)
        assert entry.description == "Index test"
        assert entry.state == EngramState.CANDIDATE
        assert entry.quality_score == 0.7
        assert entry.tags == ["test"]
        assert entry.version == 2


class TestStoreIndex:
    def test_empty_index(self) -> None:
        idx = StoreIndex()
        assert idx.version == 1
        assert idx.engrams == {}

    def test_index_with_entries(self) -> None:
        now = datetime.now(tz=UTC)
        entry = IndexEntry(
            description="test",
            state=EngramState.DRAFT,
            trust=TrustLevel.AGENT_CREATED,
            quality_score=0.5,
            tags=[],
            patterns=[],
            projects=[],
            files=[],
            updated=now,
            version=1,
        )
        idx = StoreIndex(engrams={"test-slug": entry})
        assert "test-slug" in idx.engrams


class TestScanModels:
    def test_scan_result(self) -> None:
        r = ScanResult(
            severity="critical",
            category="credential",
            pattern_id="CRED-001",
            matched_text="AKIA...",
            line_number=5,
            message="AWS access key detected",
        )
        assert r.severity == "critical"

    def test_scan_verdict_allow(self) -> None:
        v = ScanVerdict(action="allow", results=[])
        assert v.action == "allow"

    def test_scan_verdict_block(self) -> None:
        r = ScanResult(
            severity="critical",
            category="credential",
            pattern_id="CRED-001",
            matched_text="AKIA...",
            line_number=5,
            message="AWS access key detected",
        )
        v = ScanVerdict(action="block", results=[r])
        assert v.action == "block"
        assert len(v.results) == 1
