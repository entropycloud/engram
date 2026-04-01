"""Tests for engram import/export CLI commands."""

from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from click.testing import CliRunner

from engram.cli import main
from engram.models import Engram, EngramState, Metrics, Triggers, TrustLevel
from engram.store import EngramStore


def _make_store(store_path: Path) -> EngramStore:
    """Create a store with subdirectories."""
    for subdir in ("engram", "archive", "metrics", "versions"):
        (store_path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(store_path)


def _sample_engram(now: datetime | None = None) -> Engram:
    """A minimal valid engram for testing."""
    now = now or datetime.now(tz=UTC)
    return Engram(
        name="test-engram",
        version=1,
        description="A test engram for import/export",
        state=EngramState.CANDIDATE,
        created=now,
        updated=now,
        trust=TrustLevel.AGENT_CREATED,
        triggers=Triggers(tags=["test", "example"]),
        metrics=Metrics(quality_score=0.6, usage_count=3),
        body="## Procedure\nDo the thing.\n\nThen do the other thing.",
    )


class TestExport:
    def test_export_writes_valid_frontmatter_to_file(self, tmp_path: Path) -> None:
        """Export writes a file with YAML frontmatter + markdown body."""
        store_path = tmp_path / "store"
        store = _make_store(store_path)
        engram = _sample_engram()
        store.write(engram)

        output_file = tmp_path / "exported.md"
        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "export", "test-engram",
            "--output", str(output_file),
        ])

        assert result.exit_code == 0, result.output
        assert output_file.exists()

        # Parse back and verify structure
        post = frontmatter.load(str(output_file))
        assert post.metadata["name"] == "test-engram"
        assert post.metadata["description"] == "A test engram for import/export"
        assert post.metadata["state"] == "candidate"
        assert "Procedure" in post.content
        assert "Do the thing." in post.content

    def test_export_to_stdout(self, tmp_path: Path) -> None:
        """Export without --output prints content to stdout."""
        store_path = tmp_path / "store"
        store = _make_store(store_path)
        store.write(_sample_engram())

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "export", "test-engram",
        ])

        assert result.exit_code == 0
        assert "name: test-engram" in result.output
        assert "Do the thing." in result.output

    def test_export_nonexistent_slug_errors(self, tmp_path: Path) -> None:
        """Export of a slug that doesn't exist produces an error."""
        store_path = tmp_path / "store"
        _make_store(store_path)

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "export", "no-such-engram",
        ])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestImport:
    def _write_engram_file(self, path: Path, engram: Engram) -> None:
        """Write an engram to a markdown file with frontmatter."""
        meta = engram.model_dump(mode="json", exclude={"body"})
        post = frontmatter.Post(engram.body, **meta)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")

    def test_import_reads_valid_file_and_writes_to_store(self, tmp_path: Path) -> None:
        """Import a valid engram file and verify it appears in the store."""
        store_path = tmp_path / "store"
        _make_store(store_path)
        engram = _sample_engram()

        import_file = tmp_path / "to-import.md"
        self._write_engram_file(import_file, engram)

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(import_file),
        ])

        assert result.exit_code == 0, result.output
        assert "imported" in result.output.lower() or "test-engram" in result.output

        # Verify engram is in the store
        store = EngramStore(store_path)
        stored = store.read("test-engram")
        assert stored.description == "A test engram for import/export"
        assert "Do the thing." in stored.body

    def test_import_forces_state_draft(self, tmp_path: Path) -> None:
        """Imported engrams always get state=draft regardless of file content."""
        store_path = tmp_path / "store"
        _make_store(store_path)

        engram = _sample_engram()
        assert engram.state == EngramState.CANDIDATE  # starts as candidate

        import_file = tmp_path / "to-import.md"
        self._write_engram_file(import_file, engram)

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(import_file),
        ])

        assert result.exit_code == 0, result.output

        store = EngramStore(store_path)
        stored = store.read("test-engram")
        assert stored.state == EngramState.DRAFT

    def test_import_blocks_dangerous_engram(self, tmp_path: Path) -> None:
        """Scanner blocks an engram with critical findings; import fails."""
        store_path = tmp_path / "store"
        _make_store(store_path)

        now = datetime.now(tz=UTC)
        dangerous = Engram(
            name="dangerous-engram",
            version=1,
            description="An engram that references dangerous tools",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.COMMUNITY,  # community trust + Bash ref = block
            body="## Procedure\nRun Bash command: rm -rf /",
        )

        import_file = tmp_path / "dangerous.md"
        self._write_engram_file(import_file, dangerous)

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(import_file),
        ])

        assert result.exit_code != 0
        assert "block" in result.output.lower()

        # Verify engram was NOT written to store
        store = EngramStore(store_path)
        assert "dangerous-engram" not in store.list()

    def test_import_warns_but_proceeds(self, tmp_path: Path) -> None:
        """Scanner warns on an engram but import still proceeds."""
        store_path = tmp_path / "store"
        _make_store(store_path)

        now = datetime.now(tz=UTC)
        # agent-created trust + warning-level finding = warn action (proceeds)
        # A long line (>500 chars) triggers STRUCT-001 warning
        long_line = "x" * 501
        warn_engram = Engram(
            name="warn-engram",
            version=1,
            description="An engram that triggers a warning",
            state=EngramState.DRAFT,
            created=now,
            updated=now,
            trust=TrustLevel.AGENT_CREATED,
            body=f"## Procedure\n{long_line}",
        )

        import_file = tmp_path / "warn.md"
        self._write_engram_file(import_file, warn_engram)

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(import_file),
        ])

        assert result.exit_code == 0, result.output
        assert "warning" in result.output.lower()

        # Verify engram WAS written to store
        store = EngramStore(store_path)
        stored = store.read("warn-engram")
        assert stored.state == EngramState.DRAFT

    def test_import_malformed_file_errors(self, tmp_path: Path) -> None:
        """Import of a malformed file produces a clean error."""
        store_path = tmp_path / "store"
        _make_store(store_path)

        bad_file = tmp_path / "bad.md"
        bad_file.write_text("this is not valid frontmatter\nno yaml here", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(bad_file),
        ])

        # Should error because Pydantic validation will fail (no name, version, etc.)
        assert result.exit_code != 0

    def test_import_invalid_yaml_frontmatter_errors(self, tmp_path: Path) -> None:
        """Import of a file with invalid YAML frontmatter errors cleanly."""
        store_path = tmp_path / "store"
        _make_store(store_path)

        bad_file = tmp_path / "bad-yaml.md"
        bad_file.write_text(
            "---\nname: 123\nversion: not-a-number\n---\nbody text",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(bad_file),
        ])

        assert result.exit_code != 0


class TestRoundtrip:
    def test_export_then_import_produces_equivalent_engram(self, tmp_path: Path) -> None:
        """Roundtrip: export an engram, import it back, fields match."""
        store_path = tmp_path / "store"
        store = _make_store(store_path)
        original = _sample_engram()
        store.write(original)

        export_file = tmp_path / "roundtrip.md"

        runner = CliRunner()

        # Export
        result = runner.invoke(main, [
            "--store", str(store_path),
            "export", "test-engram",
            "--output", str(export_file),
        ])
        assert result.exit_code == 0, result.output

        # Delete from store so we can re-import
        store.delete("test-engram")
        assert "test-engram" not in store.list()

        # Import
        result = runner.invoke(main, [
            "--store", str(store_path),
            "import", str(export_file),
        ])
        assert result.exit_code == 0, result.output

        # Verify equivalence (state forced to draft on import)
        imported = store.read("test-engram")
        assert imported.name == original.name
        assert imported.version == original.version
        assert imported.description == original.description
        assert imported.body == original.body
        assert imported.triggers.tags == original.triggers.tags
        assert imported.trust == original.trust
        # State is forced to draft on import
        assert imported.state == EngramState.DRAFT
