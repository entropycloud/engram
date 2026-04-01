"""Tests for formatting helpers — covering optional field branches."""

from datetime import UTC, datetime

from engram.formatting import format_engram_detail, format_engram_table
from engram.models import (
    Engram,
    EngramState,
    IndexEntry,
    Lineage,
    Metrics,
    Triggers,
    TrustLevel,
)


def _make_full_engram() -> Engram:
    now = datetime.now(tz=UTC)
    return Engram(
        name="full-engram",
        version=2,
        description="A fully populated engram",
        state=EngramState.CANDIDATE,
        created=now,
        updated=now,
        supersedes="old-engram",
        superseded_by="newer-engram",
        trust=TrustLevel.AGENT_CREATED,
        triggers=Triggers(
            tags=["deploy", "migration"],
            patterns=["deploy|restart"],
            projects=["/data/dev/myapp/*"],
            files=["**/alembic.ini"],
        ),
        metrics=Metrics(
            usage_count=10,
            success_count=8,
            override_count=1,
            quality_score=0.8,
            streak=3,
            last_used=now,
        ),
        lineage=Lineage(
            parent="parent-engram",
            created_from="session-abc",
            creation_reason="discovered during deploy",
        ),
        allowed_tools=["Bash", "Read"],
        body="## Procedure\nDo the thing.",
    )


class TestFormatEngramDetail:
    def test_full_engram_includes_all_fields(self) -> None:
        output = format_engram_detail(_make_full_engram())
        assert "Supersedes:" in output
        assert "old-engram" in output
        assert "Superseded by:" in output
        assert "newer-engram" in output
        assert "Tags:" in output
        assert "deploy, migration" in output
        assert "Patterns:" in output
        assert "Projects:" in output
        assert "Files:" in output
        assert "Allowed tools:" in output
        assert "Last used:" in output
        assert "Lineage:" in output
        assert "Parent:" in output
        assert "parent-engram" in output
        assert "From:" in output
        assert "session-abc" in output
        assert "Reason:" in output
        assert "discovered during deploy" in output
        assert "## Procedure" in output

    def test_minimal_engram(self) -> None:
        now = datetime.now(tz=UTC)
        e = Engram(
            name="minimal",
            version=1,
            description="Minimal",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            allowed_tools=[],
            body="body",
        )
        output = format_engram_detail(e)
        assert "Supersedes:" not in output
        assert "Lineage:" not in output
        assert "Last used:" not in output
        assert "Allowed tools:" not in output


class TestFormatEngramTable:
    def test_table_with_entries(self) -> None:
        now = datetime.now(tz=UTC)
        entries = {
            "test-one": IndexEntry(
                description="Test one",
                state=EngramState.DRAFT,
                trust=TrustLevel.AGENT_CREATED,
                quality_score=0.5,
                tags=[],
                patterns=[],
                projects=[],
                files=[],
                updated=now,
                version=1,
            ),
        }
        output = format_engram_table(entries)
        assert "test-one" in output
        assert "1 engram(s)" in output
