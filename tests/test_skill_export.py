"""Tests for skill export — template rendering and CLI command."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from click.testing import CliRunner

from engram.cli import main
from engram.models import Engram, EngramState, TrustLevel
from engram.reviewer import EngramReviewer
from engram.store import EngramStore


def _make_store(tmp_path: Path) -> EngramStore:
    """Create a store with standard subdirectories."""
    store_path = tmp_path / "store"
    for subdir in ("engram", "archive", "metrics", "versions"):
        (store_path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(store_path)


def _make_engram(name: str = "test-engram", **kwargs: object) -> Engram:
    """Create a test engram with sensible defaults."""
    now = datetime.now(tz=UTC)
    defaults: dict[str, object] = dict(
        name=name,
        version=1,
        description=f"Test engram: {name}",
        state=EngramState.STABLE,
        created=now,
        updated=now,
        trust=TrustLevel.VERIFIED,
        allowed_tools=["Bash", "Read", "Edit"],
        body="## Procedure\n\nDo the thing step by step.",
    )
    defaults.update(kwargs)
    return Engram(**defaults)


# ------------------------------------------------------------------
# Template rendering tests
# ------------------------------------------------------------------


class TestRenderSkillTemplate:
    """Tests for EngramReviewer.render_skill_template."""

    def test_renders_valid_yaml_frontmatter(self, tmp_path: Path) -> None:
        """Rendered SKILL.md has valid YAML frontmatter between --- delimiters."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        engram = _make_engram()

        result = reviewer.render_skill_template(engram)

        # Must start and end frontmatter with ---
        assert result.startswith("---\n")
        # Extract frontmatter
        parts = result.split("---\n", 2)
        assert len(parts) >= 3, "Expected frontmatter between --- delimiters"
        frontmatter = yaml.safe_load(parts[1])
        assert isinstance(frontmatter, dict)

    def test_name_from_engram(self, tmp_path: Path) -> None:
        """The name field in frontmatter comes from the engram name."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        engram = _make_engram(name="my-cool-skill")

        result = reviewer.render_skill_template(engram)
        fm = yaml.safe_load(result.split("---\n", 2)[1])
        assert fm["name"] == "my-cool-skill"

    def test_description_from_engram(self, tmp_path: Path) -> None:
        """The description field comes from the engram description."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        engram = _make_engram(description="A really useful skill")

        result = reviewer.render_skill_template(engram)
        fm = yaml.safe_load(result.split("---\n", 2)[1])
        assert fm["description"] == "A really useful skill"

    def test_allowed_tools_from_engram(self, tmp_path: Path) -> None:
        """The allowed-tools field comes from engram allowed_tools."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        engram = _make_engram(allowed_tools=["Bash", "Read", "Write"])

        result = reviewer.render_skill_template(engram)
        fm = yaml.safe_load(result.split("---\n", 2)[1])
        assert fm["allowed-tools"] == ["Bash", "Read", "Write"]

    def test_body_preserved(self, tmp_path: Path) -> None:
        """The engram body content appears after the title heading."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        body_text = "## Steps\n\n1. First do X\n2. Then do Y\n3. Finally Z"
        engram = _make_engram(body=body_text)

        result = reviewer.render_skill_template(engram)
        # Body text must appear in the output after the frontmatter
        assert body_text in result

    def test_empty_allowed_tools(self, tmp_path: Path) -> None:
        """Empty allowed_tools renders as empty list."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        engram = _make_engram(allowed_tools=[])

        result = reviewer.render_skill_template(engram)
        fm = yaml.safe_load(result.split("---\n", 2)[1])
        assert fm["allowed-tools"] == []

    def test_title_heading_from_name(self, tmp_path: Path) -> None:
        """The body section starts with a title heading derived from the name."""
        store = _make_store(tmp_path)
        reviewer = EngramReviewer(store)
        engram = _make_engram(name="my-cool-skill")

        result = reviewer.render_skill_template(engram)
        parts = result.split("---\n", 2)
        body_section = parts[2].lstrip("\n")
        # Should have a title heading
        assert body_section.startswith("# my-cool-skill\n")


# ------------------------------------------------------------------
# CLI export-skill tests
# ------------------------------------------------------------------


class TestExportSkillCLI:
    """Tests for the 'export-skill' CLI command."""

    def test_export_to_stdout(self, tmp_path: Path) -> None:
        """export-skill prints SKILL.md content to stdout."""
        store = _make_store(tmp_path)
        engram = _make_engram(name="stdout-test")
        store.write(engram)

        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store.root), "export-skill", "stdout-test"])
        assert result.exit_code == 0
        assert "---" in result.output
        assert "name: stdout-test" in result.output

    def test_export_to_file(self, tmp_path: Path) -> None:
        """export-skill with --output writes SKILL.md to a file."""
        store = _make_store(tmp_path)
        engram = _make_engram(name="file-test")
        store.write(engram)

        output_file = tmp_path / "output.md"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--store", str(store.root), "export-skill", "file-test", "--output", str(output_file)],
        )
        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        assert "name: file-test" in content
        assert "---" in content

    def test_nonexistent_slug_errors(self, tmp_path: Path) -> None:
        """export-skill with a nonexistent slug exits with error."""
        store = _make_store(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store.root), "export-skill", "no-such-engram"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_export_file_message(self, tmp_path: Path) -> None:
        """export-skill with --output shows a confirmation message."""
        store = _make_store(tmp_path)
        engram = _make_engram(name="msg-test")
        store.write(engram)

        output_file = tmp_path / "skill.md"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--store", str(store.root), "export-skill", "msg-test", "--output", str(output_file)],
        )
        assert result.exit_code == 0
        assert "msg-test" in result.output
