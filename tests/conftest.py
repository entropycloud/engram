"""Shared test fixtures for Engram tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_store(tmp_path: Path) -> Path:
    """Create a temporary engram store directory with full subdirectory structure."""
    for subdir in ("engram", "archive", "metrics", "versions"):
        (tmp_path / subdir).mkdir()
    return tmp_path


@pytest.fixture
def sample_engram_path() -> Path:
    """Path to sample engram fixtures."""
    return Path(__file__).parent / "fixtures" / "sample_engrams"
