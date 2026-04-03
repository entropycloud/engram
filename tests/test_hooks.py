"""Tests for injection tracking functions in engram.hooks."""

from __future__ import annotations

import json
from pathlib import Path

from engram.hooks import (
    cleanup_session_file,
    read_session_injections,
    record_injection,
)


class TestRecordInjection:
    def test_record_injection_creates_file(self, tmp_store: Path) -> None:
        """record_injection creates the JSONL file with correct content."""
        record_injection(tmp_store, "sess-1", ["engram-a", "engram-b"])

        path = tmp_store / "metrics" / "_inj_sess-1.jsonl"
        assert path.exists()

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert "ts" in data
        assert data["slugs"] == ["engram-a", "engram-b"]

    def test_record_injection_accumulates(self, tmp_store: Path) -> None:
        """Multiple calls append separate lines to the same file."""
        record_injection(tmp_store, "sess-1", ["engram-a"])
        record_injection(tmp_store, "sess-1", ["engram-b"])

        path = tmp_store / "metrics" / "_inj_sess-1.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["slugs"] == ["engram-a"]
        assert second["slugs"] == ["engram-b"]


class TestReadSessionInjections:
    def test_read_session_injections_deduplicates(self, tmp_store: Path) -> None:
        """Same slug appearing in multiple records is returned only once."""
        record_injection(tmp_store, "sess-1", ["engram-a", "engram-b"])
        record_injection(tmp_store, "sess-1", ["engram-b", "engram-c"])

        slugs = read_session_injections(tmp_store, "sess-1")
        assert slugs == ["engram-a", "engram-b", "engram-c"]

    def test_read_session_injections_empty(self, tmp_store: Path) -> None:
        """Missing file returns an empty list without error."""
        slugs = read_session_injections(tmp_store, "nonexistent-session")
        assert slugs == []


class TestCleanupSessionFile:
    def test_cleanup_session_file(self, tmp_store: Path) -> None:
        """cleanup_session_file deletes the injection tracking file."""
        record_injection(tmp_store, "sess-1", ["engram-a"])
        path = tmp_store / "metrics" / "_inj_sess-1.jsonl"
        assert path.exists()

        cleanup_session_file(tmp_store, "sess-1")
        assert not path.exists()

    def test_cleanup_missing_file_no_error(self, tmp_store: Path) -> None:
        """Cleaning up a nonexistent file does not raise."""
        cleanup_session_file(tmp_store, "nonexistent-session")
