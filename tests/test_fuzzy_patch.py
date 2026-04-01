"""Tests for Engram fuzzy patch engine — written before implementation (TDD)."""

from datetime import UTC, datetime

from engram.fuzzy_patch import (
    Patch,
    PatchType,
    apply_patch,
    find_section,
    merge_triggers,
)
from engram.models import Engram, Triggers


def _make_engram(
    *,
    body: str = "## Overview\nSome content.\n",
    version: int = 1,
    tags: list[str] | None = None,
    patterns: list[str] | None = None,
    projects: list[str] | None = None,
    files: list[str] | None = None,
    description: str = "A test engram",
) -> Engram:
    """Helper to build an Engram with sensible defaults."""
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return Engram(
        name="test-engram",
        version=version,
        description=description,
        created=now,
        updated=now,
        triggers=Triggers(
            tags=tags or ["deploy"],
            patterns=patterns or ["deploy.*restart"],
            projects=projects or [],
            files=files or [],
        ),
        body=body,
    )


# ---------------------------------------------------------------------------
# find_section
# ---------------------------------------------------------------------------
class TestFindSection:
    def test_finds_section_by_exact_heading(self) -> None:
        body = "## Overview\nContent here.\n## Procedure\nSteps here.\n## Notes\nExtra.\n"
        result = find_section(body, "## Procedure")
        assert result is not None
        start, end = result
        assert body[start:end] == "## Procedure\nSteps here.\n"

    def test_returns_none_for_missing_section(self) -> None:
        body = "## Overview\nContent here.\n"
        assert find_section(body, "## Missing") is None

    def test_handles_last_section_in_document(self) -> None:
        body = "## Overview\nContent.\n## Final\nLast section content.\n"
        result = find_section(body, "## Final")
        assert result is not None
        start, end = result
        assert body[start:end] == "## Final\nLast section content.\n"

    def test_case_insensitive_matching(self) -> None:
        body = "## Overview\nContent.\n## PROCEDURE\nSteps.\n"
        result = find_section(body, "## procedure")
        assert result is not None
        start, end = result
        assert "PROCEDURE" in body[start:end]

    def test_strips_whitespace_from_heading(self) -> None:
        body = "## Overview\nContent.\n## Procedure  \nSteps.\n"
        result = find_section(body, "## Procedure")
        assert result is not None

    def test_heading_level_boundaries(self) -> None:
        """A ## section ends at another ## or # heading, not at ###."""
        body = "## Overview\nContent.\n### Subsection\nSub content.\n## Next\nNext content.\n"
        result = find_section(body, "## Overview")
        assert result is not None
        start, end = result
        section = body[start:end]
        # Should include the ### subsection since it's a deeper level
        assert "### Subsection" in section
        assert "## Next" not in section

    def test_h3_section_ends_at_h3_or_higher(self) -> None:
        body = "## Parent\n### First\nA.\n### Second\nB.\n"
        result = find_section(body, "### First")
        assert result is not None
        start, end = result
        section = body[start:end]
        assert "### First" in section
        assert "### Second" not in section

    def test_h1_section_includes_deeper_headings(self) -> None:
        """A # section includes ## subsections (they are deeper, not same/higher)."""
        body = "# Title\nIntro.\n## Section\nContent.\n"
        result = find_section(body, "# Title")
        assert result is not None
        start, end = result
        section = body[start:end]
        assert "# Title" in section
        # ## is a deeper heading, so it's included in the # section
        assert "## Section" in section

    def test_h1_section_ends_at_next_h1(self) -> None:
        body = "# Title\nIntro.\n## Sub\nContent.\n# Another\nMore.\n"
        result = find_section(body, "# Title")
        assert result is not None
        start, end = result
        section = body[start:end]
        assert "# Title" in section
        assert "## Sub" in section
        assert "# Another" not in section


# ---------------------------------------------------------------------------
# merge_triggers
# ---------------------------------------------------------------------------
class TestMergeTriggers:
    def test_union_of_tags_no_duplicates(self) -> None:
        existing = Triggers(tags=["deploy", "migration"], patterns=[], projects=[], files=[])
        result = merge_triggers(existing, {"tags": ["migration", "rollback"]})
        assert sorted(result.tags) == ["deploy", "migration", "rollback"]

    def test_union_of_patterns(self) -> None:
        existing = Triggers(tags=[], patterns=["deploy.*"], projects=[], files=[])
        result = merge_triggers(existing, {"patterns": ["deploy.*", "restart.*"]})
        assert sorted(result.patterns) == ["deploy.*", "restart.*"]

    def test_union_of_projects(self) -> None:
        existing = Triggers(tags=[], patterns=[], projects=["/proj/a"], files=[])
        result = merge_triggers(existing, {"projects": ["/proj/b"]})
        assert sorted(result.projects) == ["/proj/a", "/proj/b"]

    def test_union_of_files(self) -> None:
        existing = Triggers(tags=[], patterns=[], files=["a.py"], projects=[])
        result = merge_triggers(existing, {"files": ["a.py", "b.py"]})
        assert sorted(result.files) == ["a.py", "b.py"]

    def test_empty_updates_preserves_existing(self) -> None:
        existing = Triggers(tags=["deploy"], patterns=["x"], projects=[], files=[])
        result = merge_triggers(existing, {})
        assert result.tags == ["deploy"]
        assert result.patterns == ["x"]


# ---------------------------------------------------------------------------
# apply_patch — append
# ---------------------------------------------------------------------------
class TestApplyPatchAppend:
    def test_appends_content_at_end_of_body(self) -> None:
        engram = _make_engram(body="## Overview\nExisting content.\n")
        patch = Patch(patch_type=PatchType.APPEND, content="## Additional\nNew stuff.\n")
        result = apply_patch(engram, patch)
        assert result.body.endswith("## Additional\nNew stuff.\n")

    def test_preserves_existing_body_content(self) -> None:
        engram = _make_engram(body="## Overview\nExisting content.\n")
        patch = Patch(patch_type=PatchType.APPEND, content="## Extra\nMore.\n")
        result = apply_patch(engram, patch)
        assert "## Overview\nExisting content.\n" in result.body

    def test_adds_newline_separator_if_body_lacks_trailing_newline(self) -> None:
        engram = _make_engram(body="## Overview\nNo trailing newline")
        patch = Patch(patch_type=PatchType.APPEND, content="## Extra\nMore.\n")
        result = apply_patch(engram, patch)
        # Should have a newline between old and new content
        assert "No trailing newline\n## Extra" in result.body

    def test_does_not_modify_original_engram(self) -> None:
        engram = _make_engram(body="Original body.\n")
        patch = Patch(patch_type=PatchType.APPEND, content="Appended.\n")
        result = apply_patch(engram, patch)
        assert engram.body == "Original body.\n"
        assert result is not engram

    def test_updates_timestamp(self) -> None:
        engram = _make_engram()
        patch = Patch(patch_type=PatchType.APPEND, content="New.\n")
        result = apply_patch(engram, patch)
        assert result.updated > engram.updated


# ---------------------------------------------------------------------------
# apply_patch — replace_section
# ---------------------------------------------------------------------------
class TestApplyPatchReplaceSection:
    def test_replaces_section_with_new_content(self) -> None:
        body = "## Overview\nOld overview.\n## Procedure\nOld steps.\n## Notes\nKeep.\n"
        engram = _make_engram(body=body)
        patch = Patch(
            patch_type=PatchType.REPLACE_SECTION,
            section_heading="## Procedure",
            content="## Procedure\nNew steps.\n",
        )
        result = apply_patch(engram, patch)
        assert "New steps." in result.body
        assert "Old steps." not in result.body

    def test_preserves_content_before_and_after(self) -> None:
        body = "## Overview\nKeep before.\n## Procedure\nReplace me.\n## Notes\nKeep after.\n"
        engram = _make_engram(body=body)
        patch = Patch(
            patch_type=PatchType.REPLACE_SECTION,
            section_heading="## Procedure",
            content="## Procedure\nReplaced.\n",
        )
        result = apply_patch(engram, patch)
        assert "Keep before." in result.body
        assert "Keep after." in result.body

    def test_handles_section_at_end_of_document(self) -> None:
        body = "## Overview\nContent.\n## Final\nOld final.\n"
        engram = _make_engram(body=body)
        patch = Patch(
            patch_type=PatchType.REPLACE_SECTION,
            section_heading="## Final",
            content="## Final\nNew final.\n",
        )
        result = apply_patch(engram, patch)
        assert "New final." in result.body
        assert "Old final." not in result.body

    def test_returns_unchanged_if_section_not_found(self) -> None:
        engram = _make_engram(body="## Overview\nContent.\n")
        patch = Patch(
            patch_type=PatchType.REPLACE_SECTION,
            section_heading="## Missing",
            content="## Missing\nNew.\n",
        )
        result = apply_patch(engram, patch)
        assert result.body == engram.body

    def test_handles_different_heading_levels(self) -> None:
        body = "## Parent\nContent.\n### Child\nOld child.\n### Sibling\nKeep.\n"
        engram = _make_engram(body=body)
        patch = Patch(
            patch_type=PatchType.REPLACE_SECTION,
            section_heading="### Child",
            content="### Child\nNew child.\n",
        )
        result = apply_patch(engram, patch)
        assert "New child." in result.body
        assert "Old child." not in result.body
        assert "Keep." in result.body

    def test_updates_timestamp(self) -> None:
        body = "## Overview\nContent.\n"
        engram = _make_engram(body=body)
        patch = Patch(
            patch_type=PatchType.REPLACE_SECTION,
            section_heading="## Overview",
            content="## Overview\nNew.\n",
        )
        result = apply_patch(engram, patch)
        assert result.updated > engram.updated


# ---------------------------------------------------------------------------
# apply_patch — frontmatter_merge
# ---------------------------------------------------------------------------
class TestApplyPatchFrontmatterMerge:
    def test_merges_new_tags_union(self) -> None:
        engram = _make_engram(tags=["deploy", "migration"])
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={"triggers": {"tags": ["migration", "rollback"]}},
        )
        result = apply_patch(engram, patch)
        assert sorted(result.triggers.tags) == ["deploy", "migration", "rollback"]

    def test_merges_new_patterns_union(self) -> None:
        engram = _make_engram(patterns=["deploy.*"])
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={"triggers": {"patterns": ["restart.*"]}},
        )
        result = apply_patch(engram, patch)
        assert sorted(result.triggers.patterns) == ["deploy.*", "restart.*"]

    def test_replaces_scalar_field(self) -> None:
        engram = _make_engram(description="Old description")
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={"description": "New description"},
        )
        result = apply_patch(engram, patch)
        assert result.description == "New description"

    def test_bumps_version(self) -> None:
        engram = _make_engram(version=3)
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={"description": "Updated"},
        )
        result = apply_patch(engram, patch)
        assert result.version == 4

    def test_deep_merge_triggers(self) -> None:
        engram = _make_engram(tags=["a"], patterns=["x"], projects=["/p1"])
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={
                "triggers": {
                    "tags": ["b"],
                    "projects": ["/p2"],
                }
            },
        )
        result = apply_patch(engram, patch)
        assert sorted(result.triggers.tags) == ["a", "b"]
        assert sorted(result.triggers.projects) == ["/p1", "/p2"]
        # patterns should be preserved (not in updates)
        assert result.triggers.patterns == ["x"]

    def test_updates_timestamp(self) -> None:
        engram = _make_engram()
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={"description": "Changed"},
        )
        result = apply_patch(engram, patch)
        assert result.updated > engram.updated

    def test_does_not_modify_original(self) -> None:
        engram = _make_engram(tags=["deploy"])
        patch = Patch(
            patch_type=PatchType.FRONTMATTER_MERGE,
            frontmatter_updates={"triggers": {"tags": ["new"]}},
        )
        result = apply_patch(engram, patch)
        assert engram.triggers.tags == ["deploy"]
        assert "new" in result.triggers.tags


# ---------------------------------------------------------------------------
# apply_patch — full_rewrite
# ---------------------------------------------------------------------------
class TestApplyPatchFullRewrite:
    def test_replaces_entire_body(self) -> None:
        engram = _make_engram(body="## Old\nOld content.\n")
        patch = Patch(patch_type=PatchType.FULL_REWRITE, content="## New\nNew content.\n")
        result = apply_patch(engram, patch)
        assert result.body == "## New\nNew content.\n"

    def test_preserves_frontmatter(self) -> None:
        engram = _make_engram(description="Keep this", tags=["keep"])
        patch = Patch(patch_type=PatchType.FULL_REWRITE, content="## Rewritten\nAll new.\n")
        result = apply_patch(engram, patch)
        assert result.description == "Keep this"
        assert result.triggers.tags == ["keep"]
        assert result.name == "test-engram"

    def test_bumps_version(self) -> None:
        engram = _make_engram(version=5)
        patch = Patch(patch_type=PatchType.FULL_REWRITE, content="New body.\n")
        result = apply_patch(engram, patch)
        assert result.version == 6

    def test_updates_timestamp(self) -> None:
        engram = _make_engram()
        patch = Patch(patch_type=PatchType.FULL_REWRITE, content="New.\n")
        result = apply_patch(engram, patch)
        assert result.updated > engram.updated
