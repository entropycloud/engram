"""Tests for the Engram CLI."""

from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from engram.cli import main
from engram.models import Engram, EngramState, Metrics, Triggers, TrustLevel
from engram.store import EngramStore


def _populate_store(store_path: Path) -> EngramStore:
    """Create a store with sample engrams for testing."""
    for subdir in ("engram", "archive", "metrics", "versions"):
        (store_path / subdir).mkdir(parents=True, exist_ok=True)
    store = EngramStore(store_path)
    now = datetime.now(tz=UTC)

    store.write(Engram(
        name="deploy-migration",
        version=2,
        description="Run migrations before deploy",
        state=EngramState.CANDIDATE,
        created=now,
        updated=now,
        trust=TrustLevel.AGENT_CREATED,
        triggers=Triggers(tags=["deploy", "migration"]),
        metrics=Metrics(quality_score=0.75, usage_count=8),
        body="## Procedure\nRun alembic upgrade head first.",
    ))
    store.write(Engram(
        name="pytest-fixtures",
        version=1,
        description="Use session-scoped fixtures for DB",
        state=EngramState.DRAFT,
        created=now,
        updated=now,
        trust=TrustLevel.AGENT_CREATED,
        triggers=Triggers(tags=["test", "pytest"]),
        body="## Procedure\nScope DB fixtures to session.",
    ))
    return store


class TestCLIVersion:
    def test_version_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestCLIList:
    def test_list_all(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _populate_store(store_path)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "list"])
        assert result.exit_code == 0
        assert "deploy-migration" in result.output
        assert "pytest-fixtures" in result.output
        assert "2 engram(s)" in result.output

    def test_list_filter_by_state(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _populate_store(store_path)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "list", "--state", "candidate"])
        assert result.exit_code == 0
        assert "deploy-migration" in result.output
        assert "pytest-fixtures" not in result.output

    def test_list_filter_by_tag(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _populate_store(store_path)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "list", "--tag", "pytest"])
        assert result.exit_code == 0
        assert "pytest-fixtures" in result.output
        assert "deploy-migration" not in result.output

    def test_list_empty_store(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        for subdir in ("engram", "archive", "metrics", "versions"):
            (store_path / subdir).mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "list"])
        assert result.exit_code == 0
        assert "No engrams found" in result.output


class TestCLIView:
    def test_view_existing(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _populate_store(store_path)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "view", "deploy-migration"])
        assert result.exit_code == 0
        assert "deploy-migration (v2)" in result.output
        assert "Run migrations before deploy" in result.output
        assert "candidate" in result.output

    def test_view_nonexistent(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _populate_store(store_path)
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "view", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestCLIRebuildIndex:
    def test_rebuild_index(self, tmp_path: Path) -> None:
        store_path = tmp_path / "store"
        _populate_store(store_path)
        # Delete index
        (store_path / "index.json").unlink()
        runner = CliRunner()
        result = runner.invoke(main, ["--store", str(store_path), "rebuild-index"])
        assert result.exit_code == 0
        assert "2 engram(s)" in result.output
        assert (store_path / "index.json").exists()


# ------------------------------------------------------------------
# Pin / unpin commands
# ------------------------------------------------------------------


class TestCLIPin:
    def test_pin_command(self, tmp_path: Path) -> None:
        """engram pin <slug> sets pinned=True on the engram."""
        store_path = tmp_path / "store"
        store = _populate_store(store_path)
        runner = CliRunner()

        # Verify not pinned initially
        engram = store.read("deploy-migration")
        assert engram.pinned is False

        result = runner.invoke(
            main, ["--store", str(store_path), "pin", "deploy-migration"]
        )
        assert result.exit_code == 0
        assert "Pinned" in result.output

        # Verify pinned persisted
        engram = store.read("deploy-migration")
        assert engram.pinned is True

    def test_unpin_command(self, tmp_path: Path) -> None:
        """engram unpin <slug> sets pinned=False on the engram."""
        store_path = tmp_path / "store"
        store = _populate_store(store_path)

        # Pin first
        engram = store.read("deploy-migration")
        engram.pinned = True
        store.write(engram)

        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "unpin", "deploy-migration"]
        )
        assert result.exit_code == 0
        assert "Unpinned" in result.output

        # Verify unpinned persisted
        engram = store.read("deploy-migration")
        assert engram.pinned is False

    def test_pin_nonexistent(self, tmp_path: Path) -> None:
        """Pinning a nonexistent engram gives an error."""
        store_path = tmp_path / "store"
        _populate_store(store_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--store", str(store_path), "pin", "nonexistent"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
