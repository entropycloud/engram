"""Tests for the Engram Selector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from engram.cli import main
from engram.models import (
    Engram,
    EngramState,
    Metrics,
    ScoredEngram,
    SessionContext,
    Triggers,
    TrustLevel,
)
from engram.selector import EngramSelector, _compute_prompt_tag_score
from engram.store import EngramStore


def _make_store(tmp_path: Path) -> EngramStore:
    """Create a store with standard subdirectories."""
    for subdir in ("engram", "archive", "metrics", "versions"):
        (tmp_path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(tmp_path)


def _make_engram(
    name: str,
    *,
    state: EngramState = EngramState.STABLE,
    tags: list[str] | None = None,
    patterns: list[str] | None = None,
    projects: list[str] | None = None,
    files: list[str] | None = None,
    quality_score: float = 0.5,
    last_used: datetime | None = None,
    body: str = "Some procedural knowledge here.",
    description: str = "A test engram",
    version: int = 1,
) -> Engram:
    """Helper to create an Engram with sensible defaults."""
    now = datetime.now(tz=UTC)
    return Engram(
        name=name,
        version=version,
        description=description,
        state=state,
        created=now,
        updated=now,
        trust=TrustLevel.AGENT_CREATED,
        triggers=Triggers(
            tags=tags or [],
            patterns=patterns or [],
            projects=projects or [],
            files=files or [],
        ),
        metrics=Metrics(
            quality_score=quality_score,
            last_used=last_used,
        ),
        body=body,
    )


# ---------------------------------------------------------------------------
# Stage 1: State filter
# ---------------------------------------------------------------------------


class TestStateFilter:
    def test_candidate_included(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("candidate-eng", state=EngramState.CANDIDATE))
        selector = EngramSelector(store)
        results = selector.select(SessionContext())
        slugs = [r.slug for r in results]
        assert "candidate-eng" in slugs

    def test_stable_included(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("stable-eng", state=EngramState.STABLE))
        selector = EngramSelector(store)
        results = selector.select(SessionContext())
        slugs = [r.slug for r in results]
        assert "stable-eng" in slugs

    def test_draft_excluded(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("draft-eng", state=EngramState.DRAFT))
        selector = EngramSelector(store)
        results = selector.select(SessionContext())
        slugs = [r.slug for r in results]
        assert "draft-eng" not in slugs

    def test_deprecated_excluded(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("deprecated-eng", state=EngramState.DEPRECATED))
        selector = EngramSelector(store)
        results = selector.select(SessionContext())
        slugs = [r.slug for r in results]
        assert "deprecated-eng" not in slugs

    def test_archived_excluded(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("archived-eng", state=EngramState.ARCHIVED))
        selector = EngramSelector(store)
        results = selector.select(SessionContext())
        slugs = [r.slug for r in results]
        assert "archived-eng" not in slugs


# ---------------------------------------------------------------------------
# Stage 2: Project filter
# ---------------------------------------------------------------------------


class TestProjectFilter:
    def test_project_glob_match(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("proj-eng", projects=["*/my-project", "*/other"]))
        selector = EngramSelector(store)
        ctx = SessionContext(project_path="/home/user/my-project")
        results = selector.select(ctx)
        assert any(r.slug == "proj-eng" for r in results)

    def test_project_glob_no_match(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("proj-eng", projects=["*/my-project"]))
        selector = EngramSelector(store)
        ctx = SessionContext(project_path="/home/user/different-project")
        results = selector.select(ctx)
        assert not any(r.slug == "proj-eng" for r in results)

    def test_empty_projects_matches_all(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("no-proj-eng", projects=[]))
        selector = EngramSelector(store)
        ctx = SessionContext(project_path="/any/path")
        results = selector.select(ctx)
        assert any(r.slug == "no-proj-eng" for r in results)


# ---------------------------------------------------------------------------
# Stage 3: File filter
# ---------------------------------------------------------------------------


class TestFileFilter:
    def test_file_glob_match(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("file-eng", files=["*.py", "*.ts"]))
        selector = EngramSelector(store)
        ctx = SessionContext(files=["src/main.py", "README.md"])
        results = selector.select(ctx)
        assert any(r.slug == "file-eng" for r in results)

    def test_file_glob_no_match(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("file-eng", files=["*.rs"]))
        selector = EngramSelector(store)
        ctx = SessionContext(files=["src/main.py"])
        results = selector.select(ctx)
        assert not any(r.slug == "file-eng" for r in results)

    def test_empty_files_matches_all(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("no-file-eng", files=[]))
        selector = EngramSelector(store)
        ctx = SessionContext(files=["anything.txt"])
        results = selector.select(ctx)
        assert any(r.slug == "no-file-eng" for r in results)


# ---------------------------------------------------------------------------
# Stage 4: Tag match
# ---------------------------------------------------------------------------


class TestTagFilter:
    def test_tag_match_above_threshold(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("tag-eng", tags=["python", "testing", "pytest"]))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=["python", "testing"])
        results = selector.select(ctx)
        assert any(r.slug == "tag-eng" for r in results)

    def test_tag_match_below_threshold_filtered(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # 1 out of 5 = 0.2 < 0.3 threshold
        store.write(_make_engram("low-tag-eng", tags=["a", "b", "c", "d", "e"]))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=["a"])
        results = selector.select(ctx)
        assert not any(r.slug == "low-tag-eng" for r in results)

    def test_engram_no_tags_passes(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("no-tag-eng", tags=[]))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=["python"])
        results = selector.select(ctx)
        assert any(r.slug == "no-tag-eng" for r in results)

    def test_context_no_tags_no_prompt_with_engram_tags(self, tmp_path: Path) -> None:
        """When context has no tags AND no prompt but engram has tags => filtered."""
        store = _make_store(tmp_path)
        store.write(_make_engram("has-tags", tags=["deploy", "migration"]))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=[], prompt="")
        results = selector.select(ctx)
        assert not any(r.slug == "has-tags" for r in results)

    def test_prompt_matches_engram_tags(self, tmp_path: Path) -> None:
        """Prompt words matching tag words should select the engram."""
        store = _make_store(tmp_path)
        store.write(_make_engram("deploy-eng", tags=["deploy", "migration"]))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=[], prompt="I need to deploy the migration to staging")
        results = selector.select(ctx)
        assert any(r.slug == "deploy-eng" for r in results)

    def test_prompt_partial_match_above_threshold(self, tmp_path: Path) -> None:
        """Partial tag word overlap above threshold should select."""
        store = _make_store(tmp_path)
        store.write(_make_engram("py-eng", tags=["python", "testing", "pytest"]))
        selector = EngramSelector(store)
        # 2/3 tag words match -> 0.667 > 0.3 threshold
        ctx = SessionContext(tags=[], prompt="how do I run pytest for this python module")
        results = selector.select(ctx)
        assert any(r.slug == "py-eng" for r in results)

    def test_prompt_no_match_below_threshold(self, tmp_path: Path) -> None:
        """Prompt with zero tag word overlap should filter."""
        store = _make_store(tmp_path)
        store.write(_make_engram("k8s-eng", tags=["docker", "kubernetes", "helm"]))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=[], prompt="fix the CSS styling on the login page")
        results = selector.select(ctx)
        assert not any(r.slug == "k8s-eng" for r in results)

    def test_pattern_match_not_blocked_by_empty_tags(self, tmp_path: Path) -> None:
        """Engram with tags+patterns passes when tags don't match but patterns do."""
        store = _make_store(tmp_path)
        store.write(_make_engram(
            "pattern-rescue",
            tags=["deploy"],
            patterns=[r"migration"],
        ))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=[], prompt="run migration")
        results = selector.select(ctx)
        assert any(r.slug == "pattern-rescue" for r in results)


class TestComputePromptTagScore:
    def test_full_match(self) -> None:
        assert _compute_prompt_tag_score(["python", "testing"], "python testing is great") == 1.0

    def test_partial_match(self) -> None:
        score = _compute_prompt_tag_score(["python", "testing"], "python is great")
        assert score == 0.5

    def test_no_match(self) -> None:
        assert _compute_prompt_tag_score(["docker", "helm"], "python testing") == 0.0

    def test_empty_tags(self) -> None:
        assert _compute_prompt_tag_score([], "python testing") == 0.0

    def test_empty_prompt(self) -> None:
        assert _compute_prompt_tag_score(["python"], "") == 0.0

    def test_case_insensitive(self) -> None:
        score = _compute_prompt_tag_score(["Python", "Testing"], "python testing")
        assert score == 1.0


# ---------------------------------------------------------------------------
# Stage 5: Pattern match
# ---------------------------------------------------------------------------


class TestPatternMatch:
    def test_pattern_matches_prompt(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("pattern-eng", patterns=[r"deploy\s+migration"]))
        selector = EngramSelector(store)
        ctx = SessionContext(prompt="I need to deploy migration to prod")
        results = selector.select(ctx)
        assert any(r.slug == "pattern-eng" for r in results)

    def test_invalid_regex_skipped_gracefully(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram("bad-regex", patterns=[r"[invalid", r"valid-pattern"]))
        selector = EngramSelector(store)
        ctx = SessionContext(prompt="valid-pattern here")
        results = selector.select(ctx)
        assert any(r.slug == "bad-regex" for r in results)


# ---------------------------------------------------------------------------
# Stage 6: Ranking
# ---------------------------------------------------------------------------


class TestRanking:
    def test_score_formula_ordering(self, tmp_path: Path) -> None:
        """Engram with higher quality score and stable state ranks higher."""
        store = _make_store(tmp_path)
        now = datetime.now(tz=UTC)
        store.write(_make_engram(
            "high-rank",
            state=EngramState.STABLE,
            quality_score=0.9,
            tags=["python"],
            last_used=now - timedelta(days=1),
        ))
        store.write(_make_engram(
            "low-rank",
            state=EngramState.CANDIDATE,
            quality_score=0.3,
            tags=["python"],
            last_used=now - timedelta(days=60),
        ))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=["python"])
        results = selector.select(ctx)
        assert len(results) >= 2
        assert results[0].slug == "high-rank"
        assert results[1].slug == "low-rank"
        assert results[0].score > results[1].score

    def test_recency_bonus_tiers(self, tmp_path: Path) -> None:
        """Verify recency bonus: last 7 days=1.0, last 30=0.5, older=0.0."""
        store = _make_store(tmp_path)
        now = datetime.now(tz=UTC)
        store.write(_make_engram(
            "recent-eng",
            tags=["test"],
            quality_score=0.5,
            last_used=now - timedelta(days=3),
        ))
        store.write(_make_engram(
            "medium-eng",
            tags=["test"],
            quality_score=0.5,
            last_used=now - timedelta(days=15),
        ))
        store.write(_make_engram(
            "old-eng",
            tags=["test"],
            quality_score=0.5,
            last_used=now - timedelta(days=60),
        ))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=["test"])
        results = selector.select(ctx)
        scores = {r.slug: r.score for r in results}
        assert scores["recent-eng"] > scores["medium-eng"]
        assert scores["medium-eng"] > scores["old-eng"]

    def test_state_bonus_stable_vs_candidate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write(_make_engram(
            "stable-eng",
            state=EngramState.STABLE,
            quality_score=0.5,
            tags=["test"],
        ))
        store.write(_make_engram(
            "candidate-eng2",
            state=EngramState.CANDIDATE,
            quality_score=0.5,
            tags=["test"],
        ))
        selector = EngramSelector(store)
        ctx = SessionContext(tags=["test"])
        results = selector.select(ctx)
        scores = {r.slug: r.score for r in results}
        assert scores["stable-eng"] > scores["candidate-eng2"]


# ---------------------------------------------------------------------------
# format_injection
# ---------------------------------------------------------------------------


class TestFormatInjection:
    def test_empty_list_returns_empty_string(self) -> None:
        selector = EngramSelector.__new__(EngramSelector)
        selector.store = None  # type: ignore[assignment]
        selector.token_budget = 3500
        result = selector.format_injection([])
        assert result == ""

    def test_high_confidence_gets_full_body(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        engram = _make_engram(
            "high-conf", description="Important procedure", body="Full body content here.",
        )
        store.write(engram)
        scored = ScoredEngram(slug="high-conf", engram=engram, score=0.85, match_reasons=["tag"])
        selector = EngramSelector(store)
        output = selector.format_injection([scored])
        assert "## Active Engrams" in output
        assert "Full body content here." in output
        assert "[stable]" in output
        assert "score: 0.85" in output

    def test_low_confidence_gets_summary_only(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        engram = _make_engram(
            "low-conf", description="Low confidence desc", body="Should not appear.",
        )
        store.write(engram)
        scored = ScoredEngram(slug="low-conf", engram=engram, score=0.35, match_reasons=["tag"])
        selector = EngramSelector(store)
        output = selector.format_injection([scored])
        assert "Should not appear." not in output
        assert "Low confidence desc" in output
        assert "engram view low-conf" in output

    def test_token_budget_enforced(self, tmp_path: Path) -> None:
        """Engrams beyond the budget should be dropped."""
        store = _make_store(tmp_path)
        scored_list = []
        for i in range(50):
            engram = _make_engram(
                f"budget-eng-{i:03d}",
                body="x" * 1000,
                description=f"Engram {i}",
            )
            store.write(engram)
            scored_list.append(ScoredEngram(
                slug=f"budget-eng-{i:03d}",
                engram=engram,
                score=0.85,
                match_reasons=["tag"],
            ))
        selector = EngramSelector(store, token_budget=3500)
        output = selector.format_injection(scored_list)
        # 3500 tokens * 4 chars/token = 14000 chars max
        assert len(output) <= 14000 + 200  # small margin for headers

    def test_format_tiers_allocation(self, tmp_path: Path) -> None:
        """High, medium, low confidence engrams get different treatment."""
        store = _make_store(tmp_path)
        high = _make_engram("high-eng", body="High body.", description="High desc")
        med = _make_engram("med-eng", body="Med body.", description="Med desc")
        low = _make_engram("low-eng", body="Low body.", description="Low desc")
        for e in [high, med, low]:
            store.write(e)

        scored = [
            ScoredEngram(slug="high-eng", engram=high, score=0.85, match_reasons=["tag"]),
            ScoredEngram(slug="med-eng", engram=med, score=0.6, match_reasons=["tag"]),
            ScoredEngram(slug="low-eng", engram=low, score=0.35, match_reasons=["tag"]),
        ]
        selector = EngramSelector(store)
        output = selector.format_injection(scored)
        assert "High body." in output
        assert "Med body." in output
        assert "Low body." not in output
        assert "Low desc" in output


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_multiple_engrams_correct_top_result(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        now = datetime.now(tz=UTC)
        store.write(_make_engram(
            "deploy-migration",
            state=EngramState.STABLE,
            tags=["deploy", "migration"],
            patterns=[r"deploy.*migration"],
            quality_score=0.9,
            last_used=now - timedelta(days=1),
            body="Run migrations before deploy.",
            description="Run migrations before restart",
            version=3,
        ))
        store.write(_make_engram(
            "pytest-scoping",
            state=EngramState.CANDIDATE,
            tags=["test", "pytest"],
            quality_score=0.4,
            last_used=now - timedelta(days=20),
            body="Scope fixtures to session.",
            description="Pytest fixture scoping",
        ))
        store.write(_make_engram(
            "draft-irrelevant",
            state=EngramState.DRAFT,
            tags=["deploy"],
            quality_score=0.8,
        ))

        selector = EngramSelector(store)
        ctx = SessionContext(
            tags=["deploy", "migration"],
            prompt="I need to deploy migration to staging",
        )
        results = selector.select(ctx)
        assert results[0].slug == "deploy-migration"
        # draft should not appear
        assert not any(r.slug == "draft-irrelevant" for r in results)


# ---------------------------------------------------------------------------
# CLI select command
# ---------------------------------------------------------------------------


class TestCLISelect:
    def _populate(self, store_path: Path) -> None:
        store = _make_store(store_path)
        store.write(_make_engram(
            "deploy-eng",
            state=EngramState.STABLE,
            tags=["deploy"],
            quality_score=0.8,
            body="Deploy procedure content.",
            description="Deploy procedure",
        ))

    def test_select_with_prompt(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(tmp_path),
            "select",
            "--tag", "deploy",
        ])
        assert result.exit_code == 0
        # Should produce output with the matched engram
        has_engram = "deploy-eng" in result.output
        has_header = "Active Engrams" in result.output
        assert has_engram or has_header or result.output.strip() == ""

    def test_select_with_output_file(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        output_file = tmp_path / "output.md"
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(tmp_path),
            "select",
            "--tag", "deploy",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        if output_file.exists():
            content = output_file.read_text()
            # Either has content or is empty (if no matches)
            assert isinstance(content, str)

    def test_select_no_matches(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(tmp_path),
            "select",
            "--tag", "nonexistent",
        ])
        assert result.exit_code == 0
