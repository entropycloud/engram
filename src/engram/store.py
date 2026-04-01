"""Engram store — atomic read/write/lock/index operations."""

from __future__ import annotations

import builtins
import os
from pathlib import Path

import frontmatter
from filelock import FileLock

from engram.models import Engram, IndexEntry, StoreIndex


def _engram_to_post(engram: Engram) -> frontmatter.Post:
    """Convert an Engram to a python-frontmatter Post for serialization."""
    meta = engram.model_dump(mode="json", exclude={"body"})
    return frontmatter.Post(engram.body, **meta)


def _post_to_engram(post: frontmatter.Post) -> Engram:
    """Convert a python-frontmatter Post to an Engram."""
    meta = dict(post.metadata)
    meta["body"] = post.content
    return Engram.model_validate(meta)


class EngramStore:
    """File-based engram store with atomic writes and index management."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._engram_dir = root / "engram"
        self._archive_dir = root / "archive"
        self._metrics_dir = root / "metrics"
        self._versions_dir = root / "versions"
        self._index_path = root / "index.json"
        self._lock_path = root / "store.lock"
        self._lock = FileLock(self._lock_path, timeout=5)

    def read(self, slug: str) -> Engram:
        """Read an engram by slug. Raises FileNotFoundError if not found."""
        path = self._engram_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Engram not found: {slug}")
        post = frontmatter.load(path)
        return _post_to_engram(post)

    def write(self, engram: Engram) -> None:
        """Atomic write: lock -> tmp -> fsync -> rename -> rebuild index -> unlock."""
        post = _engram_to_post(engram)
        content = frontmatter.dumps(post)

        with self._lock:
            tmp_path = self._engram_dir / f"{engram.name}.md.tmp"
            final_path = self._engram_dir / f"{engram.name}.md"

            tmp_path.write_text(content, encoding="utf-8")
            # fsync the tmp file
            fd = os.open(str(tmp_path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

            # Atomic rename
            tmp_path.rename(final_path)

            # Rebuild index
            self._rebuild_index_unlocked()

    def delete(self, slug: str) -> None:
        """Delete an engram and remove from index."""
        path = self._engram_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Engram not found: {slug}")
        with self._lock:
            path.unlink()
            self._rebuild_index_unlocked()

    def list(self) -> list[str]:
        """List all engram slugs in the store."""
        return sorted(
            p.stem for p in self._engram_dir.glob("*.md") if not p.name.endswith(".tmp")
        )

    def read_index(self) -> StoreIndex:
        """Read the index file. Returns empty index if file doesn't exist."""
        if not self._index_path.exists():
            return StoreIndex(engrams={})
        return StoreIndex.model_validate_json(self._index_path.read_text(encoding="utf-8"))

    def rebuild_index(self) -> StoreIndex:
        """Force rebuild of index.json from engram files."""
        with self._lock:
            return self._rebuild_index_unlocked()

    def _rebuild_index_unlocked(self) -> StoreIndex:
        """Rebuild index — must be called with lock held."""
        index = StoreIndex(engrams={})
        for path in sorted(self._engram_dir.glob("*.md")):
            if path.name.endswith(".tmp"):
                continue
            try:
                post = frontmatter.load(path)
                engram = _post_to_engram(post)
                index.engrams[engram.name] = IndexEntry.from_engram(engram)
            except Exception:
                # Skip malformed engrams during index rebuild
                continue

        # Atomic write of index
        tmp_index = self._index_path.with_suffix(".json.tmp")
        tmp_index.write_text(index.model_dump_json(indent=2), encoding="utf-8")
        fd = os.open(str(tmp_index), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp_index.rename(self._index_path)
        return index

    def move_to_archive(self, slug: str) -> None:
        """Move an engram to the archive directory and remove from index."""
        src = self._engram_dir / f"{slug}.md"
        if not src.exists():
            raise FileNotFoundError(f"Engram not found: {slug}")
        dst = self._archive_dir / f"{slug}.md"
        with self._lock:
            src.rename(dst)
            self._rebuild_index_unlocked()

    def save_version(self, slug: str, version: int) -> None:
        """Save a snapshot of the current engram as a version."""
        src = self._engram_dir / f"{slug}.md"
        if not src.exists():
            raise FileNotFoundError(f"Engram not found: {slug}")
        version_dir = self._versions_dir / slug
        version_dir.mkdir(parents=True, exist_ok=True)
        dst = version_dir / f"v{version}.md"
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    def get_version(self, slug: str, version: int) -> Engram:
        """Read a specific version of an engram."""
        path = self._versions_dir / slug / f"v{version}.md"
        if not path.exists():
            raise FileNotFoundError(f"Version {version} of {slug} not found")
        post = frontmatter.load(path)
        return _post_to_engram(post)

    def list_versions(self, slug: str) -> "builtins.list[int]":  # noqa: UP037
        """List available version numbers for an engram."""
        version_dir = self._versions_dir / slug
        if not version_dir.exists():
            return []
        versions: list[int] = []
        for p in version_dir.glob("v*.md"):
            try:
                versions.append(int(p.stem[1:]))
            except ValueError:
                continue
        return sorted(versions)

    def cleanup_tmp_files(self) -> int:
        """Remove orphaned .tmp files. Returns count of files removed."""
        removed = 0
        for tmp_file in self._engram_dir.glob("*.tmp"):
            tmp_file.unlink()
            removed += 1
        return removed


class MultiStore:
    """Two-store resolution: project-level wins on slug collision."""

    def __init__(self, project_store: EngramStore, global_store: EngramStore) -> None:
        self.project_store = project_store
        self.global_store = global_store

    def read(self, slug: str) -> Engram:
        """Read an engram, preferring project store on collision."""
        try:
            return self.project_store.read(slug)
        except FileNotFoundError:
            return self.global_store.read(slug)

    def list(self) -> list[str]:
        """List all unique engram slugs from both stores."""
        project_slugs = set(self.project_store.list())
        global_slugs = set(self.global_store.list())
        return sorted(project_slugs | global_slugs)

    def merged_index(self) -> StoreIndex:
        """Merge indexes with project-level taking priority on collision."""
        global_idx = self.global_store.read_index()
        project_idx = self.project_store.read_index()
        merged = StoreIndex(engrams={**global_idx.engrams, **project_idx.engrams})
        return merged
