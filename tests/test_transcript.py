"""Tests for session transcript loading and filtering."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engram.reviewer import EngramReviewer
from engram.store import EngramStore


def _make_jsonl_session(path: Path, records: list[dict]) -> None:
    """Write a list of dicts as JSONL to path."""
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _make_user_record(text: str, uuid: str = "u1", parent: str | None = None) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": False,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "message": {
            "role": "user",
            "content": text,
        },
    }


def _make_assistant_text_record(
    text: str, uuid: str = "a1", parent: str = "u1"
) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": False,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "message": {
            "role": "assistant",
            "content": text,
        },
    }


def _make_tool_use_record(
    tool_name: str,
    tool_input: dict,
    uuid: str = "t1",
    parent: str = "a1",
) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": False,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"toolu_{uuid}",
                    "name": tool_name,
                    "input": tool_input,
                }
            ],
        },
    }


def _make_tool_result_record(
    tool_use_id: str,
    result: str,
    uuid: str = "tr1",
    parent: str = "t1",
) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": False,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result,
                }
            ],
        },
    }


def _make_snapshot_record() -> dict:
    return {
        "type": "file-history-snapshot",
        "messageId": "snap1",
        "snapshot": {},
    }


class TestLoadTranscript:
    def test_loads_jsonl_file(self, tmp_path: Path) -> None:
        session_path = tmp_path / "session.jsonl"
        records = [
            _make_user_record("hello"),
            _make_assistant_text_record("hi there"),
        ]
        _make_jsonl_session(session_path, records)

        store_dir = tmp_path / "store"
        for sub in ("engram", "archive", "metrics", "versions"):
            (store_dir / sub).mkdir(parents=True)
        store = EngramStore(store_dir)
        reviewer = EngramReviewer(store)

        transcript = reviewer.load_transcript(session_path)
        assert len(transcript) == 2
        assert transcript[0]["type"] == "user"
        assert transcript[1]["type"] == "assistant"

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        for sub in ("engram", "archive", "metrics", "versions"):
            (store_dir / sub).mkdir(parents=True)
        store = EngramStore(store_dir)
        reviewer = EngramReviewer(store)

        with pytest.raises(FileNotFoundError):
            reviewer.load_transcript(tmp_path / "nonexistent.jsonl")

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        session_path = tmp_path / "session.jsonl"
        session_path.write_text("")

        store_dir = tmp_path / "store"
        for sub in ("engram", "archive", "metrics", "versions"):
            (store_dir / sub).mkdir(parents=True)
        store = EngramStore(store_dir)
        reviewer = EngramReviewer(store)

        transcript = reviewer.load_transcript(session_path)
        assert transcript == []

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        session_path = tmp_path / "session.jsonl"
        with open(session_path, "w") as f:
            f.write(json.dumps(_make_user_record("good")) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps(_make_assistant_text_record("also good")) + "\n")

        store_dir = tmp_path / "store"
        for sub in ("engram", "archive", "metrics", "versions"):
            (store_dir / sub).mkdir(parents=True)
        store = EngramStore(store_dir)
        reviewer = EngramReviewer(store)

        transcript = reviewer.load_transcript(session_path)
        assert len(transcript) == 2


class TestFilterTranscript:
    def _make_reviewer(self, tmp_path: Path) -> EngramReviewer:
        store_dir = tmp_path / "store"
        for sub in ("engram", "archive", "metrics", "versions"):
            (store_dir / sub).mkdir(parents=True)
        return EngramReviewer(EngramStore(store_dir))

    def test_filters_snapshots(self, tmp_path: Path) -> None:
        reviewer = self._make_reviewer(tmp_path)
        records = [
            _make_snapshot_record(),
            _make_user_record("hello"),
            _make_assistant_text_record("hi"),
        ]
        filtered = reviewer.filter_transcript(records)
        assert len(filtered) == 2
        assert all(r["type"] in ("user", "assistant") for r in filtered)

    def test_filters_sidechains(self, tmp_path: Path) -> None:
        reviewer = self._make_reviewer(tmp_path)
        sidechain = _make_assistant_text_record("sidechain")
        sidechain["isSidechain"] = True
        records = [
            _make_user_record("hello"),
            sidechain,
            _make_assistant_text_record("main"),
        ]
        filtered = reviewer.filter_transcript(records)
        assert len(filtered) == 2

    def test_extracts_tool_calls(self, tmp_path: Path) -> None:
        reviewer = self._make_reviewer(tmp_path)
        records = [
            _make_user_record("fix the bug"),
            _make_tool_use_record("Bash", {"command": "pytest"}),
            _make_tool_result_record("toolu_t1", "3 passed"),
            _make_assistant_text_record("tests pass"),
        ]
        filtered = reviewer.filter_transcript(records)
        # Should include all 4 non-snapshot, non-sidechain records
        assert len(filtered) == 4

    def test_extracts_tool_names(self, tmp_path: Path) -> None:
        reviewer = self._make_reviewer(tmp_path)
        records = [
            _make_tool_use_record("Bash", {"command": "ls"}, uuid="t1"),
            _make_tool_use_record("Read", {"file_path": "/foo"}, uuid="t2", parent="t1"),
            _make_tool_use_record("Edit", {"file_path": "/foo"}, uuid="t3", parent="t2"),
        ]
        filtered = reviewer.filter_transcript(records)
        tool_names = []
        for r in filtered:
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        tool_names.append(c["name"])
        assert tool_names == ["Bash", "Read", "Edit"]

    def test_limits_to_last_n(self, tmp_path: Path) -> None:
        reviewer = self._make_reviewer(tmp_path)
        records = [_make_user_record(f"msg-{i}", uuid=f"u{i}") for i in range(20)]
        filtered = reviewer.filter_transcript(records, last_n=5)
        assert len(filtered) == 5

    def test_empty_transcript(self, tmp_path: Path) -> None:
        reviewer = self._make_reviewer(tmp_path)
        filtered = reviewer.filter_transcript([])
        assert filtered == []


class TestBuildContextFromTranscript:
    def test_builds_session_context(self, tmp_path: Path) -> None:
        session_path = tmp_path / "session.jsonl"
        records = [
            _make_user_record("deploy the app"),
            _make_tool_use_record("Bash", {"command": "alembic upgrade head"}),
            _make_tool_result_record("toolu_t1", "OK"),
            _make_assistant_text_record("migrations complete"),
        ]
        _make_jsonl_session(session_path, records)

        store_dir = tmp_path / "store"
        for sub in ("engram", "archive", "metrics", "versions"):
            (store_dir / sub).mkdir(parents=True)
        store = EngramStore(store_dir)
        reviewer = EngramReviewer(store)

        ctx = reviewer.build_context_from_transcript(
            session_path,
            project_path="/data/dev/myapp",
            session_id="test-session",
        )
        assert ctx["project_path"] == "/data/dev/myapp"
        assert ctx["session_id"] == "test-session"
        assert len(ctx["tool_calls"]) > 0
        assert ctx["outcome"] == "unknown"
