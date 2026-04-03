"""Tests for the Engram Reviewer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from engram.cli import main
from engram.models import (
    Engram,
    EngramState,
    ReviewDecision,
    ReviewOutput,
    ReviewReport,
    TrustLevel,
)
from engram.scanner import EngramScanner
from engram.store import EngramStore


def _make_store(tmp_path: Path) -> EngramStore:
    """Create a store with standard subdirectories."""
    store_path = tmp_path / "store"
    for subdir in ("engram", "archive", "metrics", "versions"):
        (store_path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(store_path)


def _make_engram(name: str = "test-engram", **kwargs) -> Engram:  # type: ignore[no-untyped-def]
    """Create a test engram with sensible defaults."""
    now = datetime.now(tz=UTC)
    defaults = dict(
        name=name,
        version=1,
        description=f"Test engram: {name}",
        state=EngramState.DRAFT,
        created=now,
        updated=now,
        trust=TrustLevel.AGENT_CREATED,
        body="## Procedure\nDo the thing.",
    )
    defaults.update(kwargs)
    return Engram(**defaults)


# ------------------------------------------------------------------
# Model tests
# ------------------------------------------------------------------


class TestReviewModels:
    def test_review_decision_create(self) -> None:
        engram = _make_engram()
        decision = ReviewDecision(action="create", engram=engram, reason="new pattern")
        assert decision.action == "create"
        assert decision.engram is not None
        assert decision.reason == "new pattern"

    def test_review_decision_update(self) -> None:
        decision = ReviewDecision(
            action="update",
            target="some-slug",
            patch={"body_append": "new content"},
            reason="improve",
        )
        assert decision.action == "update"
        assert decision.target == "some-slug"
        assert decision.patch is not None

    def test_review_decision_skip(self) -> None:
        decision = ReviewDecision(action="skip", reason="not useful")
        assert decision.action == "skip"

    def test_review_output_defaults(self) -> None:
        output = ReviewOutput()
        assert output.decisions == []

    def test_review_output_with_decisions(self) -> None:
        decisions = [
            ReviewDecision(action="skip", reason="meh"),
            ReviewDecision(action="create", engram=_make_engram(), reason="good"),
        ]
        output = ReviewOutput(decisions=decisions)
        assert len(output.decisions) == 2

    def test_review_report_defaults(self) -> None:
        report = ReviewReport()
        assert report.created == []
        assert report.updated == []
        assert report.skipped == 0
        assert report.blocked == []
        assert report.errors == []


# ------------------------------------------------------------------
# build_review_prompt tests
# ------------------------------------------------------------------


class TestBuildReviewPrompt:
    def test_includes_session_context(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        ctx = {
            "project_path": "/my/project",
            "session_id": "sess-123",
            "tool_calls": [{"tool": "Read", "path": "foo.py"}],
            "outcome": "success",
        }
        prompt = reviewer.build_review_prompt(ctx)
        assert "sess-123" in prompt
        assert "/my/project" in prompt
        assert "success" in prompt

    def test_includes_engram_index(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        store.write(_make_engram("existing-engram", description="An existing engram"))
        reviewer = EngramReviewer(store)
        ctx = {
            "project_path": "/proj",
            "session_id": "s1",
            "tool_calls": [],
            "outcome": "unknown",
        }
        prompt = reviewer.build_review_prompt(ctx)
        assert "existing-engram" in prompt
        assert "An existing engram" in prompt

    def test_includes_output_format_instructions(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        ctx = {
            "project_path": "/proj",
            "session_id": "s1",
            "tool_calls": [],
            "outcome": "unknown",
        }
        prompt = reviewer.build_review_prompt(ctx)
        # Should mention JSON and the schema
        assert "JSON" in prompt or "json" in prompt
        assert "decisions" in prompt
        assert "create" in prompt


# ------------------------------------------------------------------
# parse_review_output tests
# ------------------------------------------------------------------


class TestParseReviewOutput:
    def test_valid_json(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        raw = json.dumps({
            "decisions": [
                {"action": "skip", "reason": "not useful"},
            ]
        })
        output = reviewer.parse_review_output(raw)
        assert len(output.decisions) == 1
        assert output.decisions[0].action == "skip"

    def test_json_in_markdown_fences(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        raw = """Here is my analysis:

```json
{
  "decisions": [
    {"action": "skip", "reason": "duplicate"}
  ]
}
```

That's my output."""
        output = reviewer.parse_review_output(raw)
        assert len(output.decisions) == 1
        assert output.decisions[0].reason == "duplicate"

    def test_invalid_json_raises_valueerror(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        with pytest.raises(ValueError):
            reviewer.parse_review_output("this is not json at all")

    def test_valid_json_bad_schema_raises_valueerror(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        # Valid JSON but wrong structure
        with pytest.raises(ValueError):
            reviewer.parse_review_output(json.dumps({"decisions": [{"action": "invalid_action"}]}))


# ------------------------------------------------------------------
# execute_decisions tests
# ------------------------------------------------------------------


class TestExecuteDecisions:
    def test_create_writes_draft_agent_created(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        scanner = EngramScanner()
        reviewer = EngramReviewer(store, scanner=scanner)

        now = datetime.now(tz=UTC)
        engram = Engram(
            name="new-pattern",
            version=1,
            description="A new pattern",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            body="## Procedure\nDo the new thing.",
        )
        output = ReviewOutput(decisions=[
            ReviewDecision(action="create", engram=engram, reason="new pattern"),
        ])
        report = reviewer.execute_decisions(output, session_id="s1")

        assert "new-pattern" in report.created
        assert report.skipped == 0

        # Verify engram was written to store
        stored = store.read("new-pattern")
        assert stored.trust == TrustLevel.AGENT_CREATED
        assert stored.state == EngramState.DRAFT

    def test_update_applies_fuzzy_patch(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        scanner = EngramScanner()
        reviewer = EngramReviewer(store, scanner=scanner)

        # Create an existing engram
        store.write(_make_engram("existing-engram"))

        output = ReviewOutput(decisions=[
            ReviewDecision(
                action="update",
                target="existing-engram",
                patch={
                    "patch_type": "append",
                    "content": "\n## Additional\nMore info.",
                },
                reason="improve",
            ),
        ])
        report = reviewer.execute_decisions(output, session_id="s1")

        assert "existing-engram" in report.updated
        updated = store.read("existing-engram")
        assert "Additional" in updated.body

    def test_scanner_blocks_unsafe_engram(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        scanner = EngramScanner()
        reviewer = EngramReviewer(store, scanner=scanner)

        now = datetime.now(tz=UTC)
        # Create an engram that references a tool not allowed for agent-created trust
        engram = Engram(
            name="unsafe-engram",
            version=1,
            description="Unsafe engram",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            body="Use Bash to run rm -rf /",
        )
        output = ReviewOutput(decisions=[
            ReviewDecision(action="create", engram=engram, reason="test"),
        ])
        report = reviewer.execute_decisions(output, session_id="s1")

        assert "unsafe-engram" in report.blocked
        assert "unsafe-engram" not in report.created
        # Should NOT be in the store
        with pytest.raises(FileNotFoundError):
            store.read("unsafe-engram")

    def test_skip_decision_counted(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)

        output = ReviewOutput(decisions=[
            ReviewDecision(action="skip", reason="not useful"),
            ReviewDecision(action="skip", reason="duplicate"),
        ])
        report = reviewer.execute_decisions(output, session_id="s1")

        assert report.skipped == 2
        assert report.created == []
        assert report.updated == []


# ------------------------------------------------------------------
# render_engram_template tests
# ------------------------------------------------------------------


class TestRenderEngramTemplate:
    def test_produces_valid_frontmatter(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)

        now = datetime.now(tz=UTC).isoformat()
        rendered = reviewer.render_engram_template(
            name="my-engram",
            version=1,
            description="A test engram",
            state="draft",
            created=now,
            updated=now,
            trust="agent-created",
            tags=["test"],
            patterns=[],
            projects=[],
            files=[],
            body="## Procedure\nDo the thing.",
        )

        assert "---" in rendered
        assert 'name: "my-engram"' in rendered
        assert "version: 1" in rendered
        assert "trust: agent-created" in rendered
        assert "## Procedure" in rendered
        assert "Do the thing." in rendered


# ------------------------------------------------------------------
# review_session tests
# ------------------------------------------------------------------


class TestReviewSession:
    def test_returns_empty_report(self, tmp_path: Path) -> None:
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        ctx = {
            "project_path": "/proj",
            "session_id": "s1",
            "tool_calls": [],
            "outcome": "unknown",
        }
        report = reviewer.review_session(ctx)
        assert isinstance(report, ReviewReport)
        assert report.created == []
        assert report.updated == []
        assert report.skipped == 0


# ------------------------------------------------------------------
# CLI review command tests
# ------------------------------------------------------------------


class TestReviewCLI:
    def test_review_command_auto_no_anthropic(self, tmp_path: Path) -> None:
        """Auto mode without anthropic installed exits cleanly."""
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "review"])
        assert result.exit_code == 0
        assert "Review prompt built" in result.output

    def test_review_command_interactive(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "review", "--mode", "interactive"],
        )
        assert result.exit_code == 0
        assert "Interactive review requires Claude Code agent" in result.output

    def test_review_command_with_session(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path), "review", "--session", "my-sess", "--dry-run",
        ])
        assert result.exit_code == 0

    def test_review_dry_run(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "review", "--dry-run"],
        )
        assert result.exit_code == 0
        # Dry run should print the actual prompt content (includes JSON/decisions instructions)
        assert "decisions" in result.output.lower() or "review" in result.output.lower()


# ------------------------------------------------------------------
# End-to-end pipeline tests (mocked LLM)
# ------------------------------------------------------------------


class TestReviewPipelineEndToEnd:
    def _make_transcript(self, tmp_path: Path) -> Path:
        """Create a minimal JSONL transcript file."""
        transcript = tmp_path / "session.jsonl"
        records = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "isSidechain": False,
                "message": {"role": "user", "content": "fix the bug"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"path": "src/main.py"}},
                    ],
                },
            },
        ]
        transcript.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )
        return transcript

    def test_auto_mode_creates_engram(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)

        transcript = self._make_transcript(tmp_path)

        now = datetime.now(tz=UTC).isoformat()
        llm_response = json.dumps({
            "decisions": [
                {
                    "action": "create",
                    "engram": {
                        "name": "bug-fix-pattern",
                        "version": 1,
                        "description": "How to fix bugs",
                        "state": "draft",
                        "created": now,
                        "updated": now,
                        "trust": "agent-created",
                        "triggers": {"tags": ["bugfix"]},
                        "body": "## Procedure\n1. Read the code\n2. Fix the bug",
                    },
                    "reason": "new pattern observed",
                }
            ]
        })

        runner = CliRunner()
        with patch("engram.llm.call_reviewer_llm", return_value=llm_response) as mock_llm:
            result = runner.invoke(main, [
                "--store", str(store_path),
                "review",
                "--transcript", str(transcript),
                "--mode", "auto",
            ])

        assert result.exit_code == 0, result.output
        assert "Created: bug-fix-pattern" in result.output
        mock_llm.assert_called_once()

        # Verify engram actually written to store
        store = EngramStore(store_path)
        engram = store.read("bug-fix-pattern")
        assert engram.description == "How to fix bugs"
        assert engram.state == EngramState.DRAFT

    def test_llm_failure_exits_cleanly(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)

        transcript = self._make_transcript(tmp_path)

        from engram.llm import LLMError

        runner = CliRunner()
        with patch("engram.llm.call_reviewer_llm", side_effect=LLMError("API timeout")):
            result = runner.invoke(main, [
                "--store", str(store_path),
                "review",
                "--transcript", str(transcript),
                "--mode", "auto",
            ])

        # Must exit 0 — hook must never fail the session
        assert result.exit_code == 0

    def test_missing_anthropic_exits_cleanly(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True, exist_ok=True)

        runner = CliRunner()
        # call_reviewer_llm raises ImportError when anthropic isn't installed
        with patch(
            "engram.llm.call_reviewer_llm",
            side_effect=ImportError("pip install engram[llm]"),
        ):
            result = runner.invoke(main, [
                "--store", str(store_path),
                "review",
                "--mode", "auto",
            ])

        assert result.exit_code == 0


# ------------------------------------------------------------------
# Injected engrams in review prompt
# ------------------------------------------------------------------


class TestBuildReviewPromptInjectedSlugs:
    def test_review_prompt_includes_injected_slugs(self, tmp_path: Path) -> None:
        """When injected_slugs are in session context, the prompt includes the section."""
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        store.write(_make_engram("active-engram", description="An active engram"))
        reviewer = EngramReviewer(store)
        ctx = {
            "project_path": "/proj",
            "session_id": "s1",
            "tool_calls": [],
            "outcome": "success",
            "injected_slugs": ["active-engram"],
        }
        prompt = reviewer.build_review_prompt(ctx)
        assert "Injected Engrams" in prompt
        assert "active-engram" in prompt
        assert "An active engram" in prompt
        assert '"evaluate"' in prompt

    def test_review_prompt_no_injected_slugs(self, tmp_path: Path) -> None:
        """Backward compat: no injected_slugs means no injected section."""
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        ctx = {
            "project_path": "/proj",
            "session_id": "s1",
            "tool_calls": [],
            "outcome": "success",
        }
        prompt = reviewer.build_review_prompt(ctx)
        assert "Injected Engrams" not in prompt


# ------------------------------------------------------------------
# execute_decisions: evaluate action
# ------------------------------------------------------------------


class TestExecuteEvaluate:
    def test_execute_evaluate_success(self, tmp_path: Path) -> None:
        """Evaluate with outcome=success records signal and updates score."""
        from engram.evaluator import EngramEvaluator
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        store.write(_make_engram("target-engram"))
        reviewer = EngramReviewer(store)

        output = ReviewOutput(decisions=[
            ReviewDecision(
                action="evaluate",
                target="target-engram",
                outcome="success",
                reason="followed the procedure",
            ),
        ])
        report = reviewer.execute_decisions(output, session_id="sess-1")

        assert "target-engram" in report.evaluated

        # Verify signal was recorded as a metric event
        evaluator = EngramEvaluator(store)
        events = evaluator.read_events("target-engram")
        assert any(e.event == "success" for e in events)

        # Verify score was updated
        engram = store.read("target-engram")
        assert engram.metrics.last_evaluated is not None

    def test_execute_evaluate_unused_is_noop(self, tmp_path: Path) -> None:
        """Evaluate with outcome=unused does nothing (not added to report)."""
        from engram.evaluator import EngramEvaluator
        from engram.reviewer import EngramReviewer

        store = _make_store(tmp_path)
        store.write(_make_engram("target-engram"))
        reviewer = EngramReviewer(store)

        output = ReviewOutput(decisions=[
            ReviewDecision(
                action="evaluate",
                target="target-engram",
                outcome="unused",
                reason="not relevant this session",
            ),
        ])
        report = reviewer.execute_decisions(output, session_id="sess-1")

        assert report.evaluated == []

        # Verify no events were recorded
        evaluator = EngramEvaluator(store)
        events = evaluator.read_events("target-engram")
        assert events == []
