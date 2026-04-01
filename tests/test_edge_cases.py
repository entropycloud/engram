"""Edge case tests for Phase 8 polish."""

import threading
from datetime import UTC, datetime
from pathlib import Path

from engram.models import Engram, EngramState, TrustLevel
from engram.store import EngramStore


def _make_engram(name: str = "test-engram", **kwargs: object) -> Engram:
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


class TestEmptyStore:
    def test_read_index_empty(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        idx = store.read_index()
        assert idx.engrams == {}

    def test_list_empty(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        assert store.list() == []

    def test_rebuild_index_empty(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        idx = store.rebuild_index()
        assert idx.engrams == {}

    def test_cleanup_tmp_empty(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        assert store.cleanup_tmp_files() == 0


class TestCorruptedFiles:
    def test_corrupted_engram_skipped_in_index_rebuild(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        # Write a valid engram
        store.write(_make_engram("valid-one"))
        # Write a corrupted file directly
        (tmp_store / "engram" / "corrupted.md").write_text("not valid yaml frontmatter {{{{")
        # Rebuild should skip the corrupted file
        idx = store.rebuild_index()
        assert "valid-one" in idx.engrams
        assert "corrupted" not in idx.engrams

    def test_corrupted_index_recoverable(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram("good-engram"))
        # Corrupt the index
        (tmp_store / "index.json").write_text("{invalid json")
        # Rebuild should recover
        idx = store.rebuild_index()
        assert "good-engram" in idx.engrams


class TestConcurrentWrites:
    def test_many_concurrent_writes(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        errors: list[Exception] = []

        def write_engram(i: int) -> None:
            try:
                store.write(_make_engram(f"concurrent-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_engram, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(store.list()) == 20

    def test_concurrent_read_write(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        store.write(_make_engram("shared"))
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(10):
                    store.read("shared")
            except Exception as e:
                errors.append(e)

        def writer() -> None:
            try:
                for i in range(10):
                    store.write(_make_engram("shared", version=i + 1))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == []


class TestUnicode:
    def test_unicode_description(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        e = _make_engram("unicode-test", description="Deploy mit Ümlauten: äöü")
        store.write(e)
        loaded = store.read("unicode-test")
        assert loaded.description == "Deploy mit Ümlauten: äöü"

    def test_unicode_body(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        e = _make_engram("unicode-body", body="## 手順\n日本語のテスト")
        store.write(e)
        loaded = store.read("unicode-body")
        assert "日本語のテスト" in loaded.body

    def test_emoji_in_body(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        e = _make_engram("emoji-test", body="## Status\n✅ Working 🚀 Fast")
        store.write(e)
        loaded = store.read("emoji-test")
        assert "✅" in loaded.body


class TestLargeEngram:
    def test_large_body(self, tmp_store: Path) -> None:
        store = EngramStore(tmp_store)
        big_body = "## Steps\n" + "\n".join(f"{i}. Step {i}" for i in range(500))
        e = _make_engram("large-engram", body=big_body)
        store.write(e)
        loaded = store.read("large-engram")
        assert "499. Step 499" in loaded.body
