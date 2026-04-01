"""Tests for EngramStore and MultiStore — written before implementation (TDD)."""

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engram.models import Engram, EngramState, TrustLevel
from engram.store import EngramStore, MultiStore


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


class TestEngramStoreWriteRead:
    def test_write_and_read_roundtrip(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        engram = _make_engram()
        store.write(engram)
        loaded = store.read("test-engram")
        assert loaded.name == engram.name
        assert loaded.description == engram.description
        assert loaded.body == engram.body
        assert loaded.version == engram.version

    def test_write_creates_file(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram())
        assert (tmp_store / "engram" / "test-engram.md").exists()

    def test_read_nonexistent_raises(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        with pytest.raises(FileNotFoundError):
            store.read("nonexistent")

    def test_write_cleans_up_tmp_on_success(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram())
        tmp_files = list(tmp_store.glob("engram/*.tmp"))
        assert tmp_files == []

    def test_overwrite_existing(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram(description="v1"))
        store.write(_make_engram(version=2, description="v2"))
        loaded = store.read("test-engram")
        assert loaded.description == "v2"
        assert loaded.version == 2


class TestEngramStoreIndex:
    def test_write_updates_index(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram())
        idx = store.read_index()
        assert "test-engram" in idx.engrams
        assert idx.engrams["test-engram"].description == "Test engram: test-engram"

    def test_rebuild_index_from_files(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram("engram-one"))
        store.write(_make_engram("engram-two"))
        # Delete index and rebuild
        index_path = tmp_store / "index.json"
        if index_path.exists():
            index_path.unlink()
        idx = store.rebuild_index()
        assert "engram-one" in idx.engrams
        assert "engram-two" in idx.engrams

    def test_empty_store_index(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        idx = store.read_index()
        assert idx.engrams == {}


class TestEngramStoreDelete:
    def test_delete_removes_file_and_index_entry(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram())
        store.delete("test-engram")
        assert not (tmp_store / "engram" / "test-engram.md").exists()
        idx = store.read_index()
        assert "test-engram" not in idx.engrams

    def test_delete_nonexistent_raises(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        with pytest.raises(FileNotFoundError):
            store.delete("nonexistent")


class TestEngramStoreList:
    def test_list_all(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram("alpha"))
        store.write(_make_engram("beta"))
        names = store.list()
        assert set(names) == {"alpha", "beta"}

    def test_list_empty_store(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        assert store.list() == []


class TestEngramStoreArchive:
    def test_move_to_archive(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram())
        store.move_to_archive("test-engram")
        assert not (tmp_store / "engram" / "test-engram.md").exists()
        assert (tmp_store / "archive" / "test-engram.md").exists()
        idx = store.read_index()
        assert "test-engram" not in idx.engrams


class TestEngramStoreVersions:
    def test_save_and_list_versions(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        engram = _make_engram()
        store.write(engram)
        store.save_version("test-engram", 1)
        versions = store.list_versions("test-engram")
        assert 1 in versions

    def test_get_version(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        engram = _make_engram(description="original")
        store.write(engram)
        store.save_version("test-engram", 1)
        # Overwrite with new version
        store.write(_make_engram(version=2, description="updated"))
        # Retrieve old version
        v1 = store.get_version("test-engram", 1)
        assert v1.description == "original"


class TestEngramStoreCleanup:
    def test_cleanup_tmp_files(self, tmp_store: Path) -> None:
        # Create orphan tmp files
        (tmp_store / "engram" / "stale.md.tmp").write_text("garbage")
        store = EngramStore(tmp_store)
        removed = store.cleanup_tmp_files()
        assert removed == 1
        assert not (tmp_store / "engram" / "stale.md.tmp").exists()


class TestEngramStoreLocking:
    def test_concurrent_writes_dont_corrupt(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        errors: list[Exception] = []

        def write_engram(i: int) -> None:
            try:
                store.write(_make_engram(f"engram-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_engram, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        names = store.list()
        assert len(names) == 10


class TestMultiStore:
    def test_project_wins_on_slug_collision(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        for d in (global_dir, project_dir):
            for sub in ("engram", "archive", "metrics", "versions"):
                (d / sub).mkdir(parents=True)

        global_store = EngramStore(global_dir)
        project_store = EngramStore(project_dir)

        global_store.write(_make_engram("shared", description="global version"))
        project_store.write(_make_engram("shared", description="project version"))

        multi = MultiStore(project_store=project_store, global_store=global_store)
        engram = multi.read("shared")
        assert engram.description == "project version"

    def test_global_engram_visible_when_no_project_override(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        for d in (global_dir, project_dir):
            for sub in ("engram", "archive", "metrics", "versions"):
                (d / sub).mkdir(parents=True)

        global_store = EngramStore(global_dir)
        project_store = EngramStore(project_dir)

        global_store.write(_make_engram("global-only", description="from global"))

        multi = MultiStore(project_store=project_store, global_store=global_store)
        engram = multi.read("global-only")
        assert engram.description == "from global"

    def test_merged_index(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        for d in (global_dir, project_dir):
            for sub in ("engram", "archive", "metrics", "versions"):
                (d / sub).mkdir(parents=True)

        global_store = EngramStore(global_dir)
        project_store = EngramStore(project_dir)

        global_store.write(_make_engram("g-engram"))
        project_store.write(_make_engram("p-engram"))

        multi = MultiStore(project_store=project_store, global_store=global_store)
        idx = multi.merged_index()
        assert "g-engram" in idx.engrams
        assert "p-engram" in idx.engrams

    def test_list_merged(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        for d in (global_dir, project_dir):
            for sub in ("engram", "archive", "metrics", "versions"):
                (d / sub).mkdir(parents=True)

        global_store = EngramStore(global_dir)
        project_store = EngramStore(project_dir)

        global_store.write(_make_engram("shared"))
        global_store.write(_make_engram("global-only"))
        project_store.write(_make_engram("shared"))
        project_store.write(_make_engram("project-only"))

        multi = MultiStore(project_store=project_store, global_store=global_store)
        names = multi.list()
        assert set(names) == {"shared", "global-only", "project-only"}
