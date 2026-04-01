"""Tests for Engram Claude Code integration install/uninstall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from engram.cli import main
from engram.install import (
    HOOK_CONFIG,
    _merge_hooks,
    _remove_hooks,
    install_claude_code_integration,
    uninstall_claude_code_integration,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a temp dir so we don't touch real ~/.claude/."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A fake project directory."""
    d = tmp_path / "myproject"
    d.mkdir()
    return d


# ------------------------------------------------------------------ #
# _merge_hooks
# ------------------------------------------------------------------ #


class TestMergeHooks:
    def test_merge_into_empty_settings(self) -> None:
        result = _merge_hooks({}, HOOK_CONFIG["hooks"])
        assert "hooks" in result
        assert "Stop" in result["hooks"]
        assert len(result["hooks"]["Stop"]) == 2

    def test_merge_preserves_existing_non_engram_hooks(self) -> None:
        existing = {
            "hooks": {
                "Stop": [
                    {"type": "command", "command": "other-tool cleanup", "timeout": 3000}
                ]
            }
        }
        result = _merge_hooks(existing, HOOK_CONFIG["hooks"])
        stop_hooks = result["hooks"]["Stop"]
        commands = [h["command"] for h in stop_hooks]
        assert "other-tool cleanup" in commands
        assert any("engram review" in c for c in commands)

    def test_merge_no_duplicates(self) -> None:
        """Running merge twice should not create duplicate hooks."""
        first = _merge_hooks({}, HOOK_CONFIG["hooks"])
        second = _merge_hooks(first, HOOK_CONFIG["hooks"])
        assert len(second["hooks"]["Stop"]) == len(first["hooks"]["Stop"])
        assert len(second["hooks"]["PostToolUse"]) == len(first["hooks"]["PostToolUse"])

    def test_merge_preserves_non_hook_settings(self) -> None:
        existing = {"model": "claude-opus-4-0-20250514", "hooks": {}}
        result = _merge_hooks(existing, HOOK_CONFIG["hooks"])
        assert result["model"] == "claude-opus-4-0-20250514"


# ------------------------------------------------------------------ #
# _remove_hooks
# ------------------------------------------------------------------ #


class TestRemoveHooks:
    def test_remove_engram_hooks(self) -> None:
        settings = _merge_hooks({}, HOOK_CONFIG["hooks"])
        result = _remove_hooks(settings, HOOK_CONFIG["hooks"])
        # All engram hooks removed, so event keys should be empty or gone
        for event_hooks in result.get("hooks", {}).values():
            for h in event_hooks:
                assert "engram" not in h.get("command", "")

    def test_remove_preserves_other_hooks(self) -> None:
        existing = {
            "hooks": {
                "Stop": [
                    {"type": "command", "command": "other-tool cleanup", "timeout": 3000}
                ]
            }
        }
        merged = _merge_hooks(existing, HOOK_CONFIG["hooks"])
        result = _remove_hooks(merged, HOOK_CONFIG["hooks"])
        stop_hooks = result["hooks"]["Stop"]
        assert len(stop_hooks) == 1
        assert stop_hooks[0]["command"] == "other-tool cleanup"

    def test_remove_from_empty_settings(self) -> None:
        result = _remove_hooks({}, HOOK_CONFIG["hooks"])
        # Should not crash
        assert isinstance(result, dict)

    def test_remove_preserves_non_hook_settings(self) -> None:
        settings = {"model": "claude-opus-4-0-20250514", "hooks": {}}
        merged = _merge_hooks(settings, HOOK_CONFIG["hooks"])
        result = _remove_hooks(merged, HOOK_CONFIG["hooks"])
        assert result["model"] == "claude-opus-4-0-20250514"


# ------------------------------------------------------------------ #
# install_claude_code_integration — global
# ------------------------------------------------------------------ #


class TestInstallGlobal:
    def test_creates_skill_file(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        skill_path = fake_home / ".claude" / "skills" / "engram" / "SKILL.md"
        assert skill_path.exists()
        content = skill_path.read_text()
        assert "Engram" in content
        assert "/engram" in content

    def test_creates_agent_file(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        agent_path = fake_home / ".claude" / "agents" / "engram-reviewer.md"
        assert agent_path.exists()
        content = agent_path.read_text()
        assert "Engram Reviewer" in content

    def test_creates_store_directories(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        store_root = fake_home / ".claude" / "engrams"
        for subdir in ("engram", "archive", "metrics", "versions"):
            assert (store_root / subdir).is_dir()

    def test_merges_hooks_into_settings(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        settings_path = fake_home / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "Stop" in settings["hooks"]
        stop_cmds = [h["command"] for h in settings["hooks"]["Stop"]]
        assert any("engram review" in c for c in stop_cmds)
        assert any("engram signal" in c for c in stop_cmds)

    def test_idempotent(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        install_claude_code_integration(global_install=True)
        settings_path = fake_home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        # No duplicates
        assert len(settings["hooks"]["Stop"]) == 2

    def test_preserves_existing_settings(self, fake_home: Path) -> None:
        settings_dir = fake_home / ".claude"
        settings_dir.mkdir(parents=True)
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(json.dumps({
            "model": "claude-opus-4-0-20250514",
            "hooks": {
                "Stop": [
                    {"type": "command", "command": "my-tool goodbye", "timeout": 2000}
                ]
            }
        }))
        install_claude_code_integration(global_install=True)
        settings = json.loads(settings_path.read_text())
        assert settings["model"] == "claude-opus-4-0-20250514"
        stop_cmds = [h["command"] for h in settings["hooks"]["Stop"]]
        assert "my-tool goodbye" in stop_cmds

    def test_report_structure(self, fake_home: Path) -> None:
        report = install_claude_code_integration(global_install=True)
        assert "created" in report
        assert "updated" in report
        assert isinstance(report["created"], list)
        assert isinstance(report["updated"], list)


# ------------------------------------------------------------------ #
# install_claude_code_integration — project
# ------------------------------------------------------------------ #


class TestInstallProject:
    def test_creates_skill_in_project(
        self, fake_home: Path, project_dir: Path
    ) -> None:
        install_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        skill_path = project_dir / ".claude" / "skills" / "engram" / "SKILL.md"
        assert skill_path.exists()

    def test_creates_agent_globally_even_for_project_install(
        self, fake_home: Path, project_dir: Path
    ) -> None:
        """Agent file is always global, even for project installs."""
        install_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        agent_path = fake_home / ".claude" / "agents" / "engram-reviewer.md"
        assert agent_path.exists()

    def test_settings_in_project_dir(
        self, fake_home: Path, project_dir: Path
    ) -> None:
        install_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        settings_path = project_dir / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

    def test_creates_project_store_dir(
        self, fake_home: Path, project_dir: Path
    ) -> None:
        install_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        store_root = project_dir / ".engram"
        for subdir in ("engram", "archive", "metrics", "versions"):
            assert (store_root / subdir).is_dir()


# ------------------------------------------------------------------ #
# uninstall_claude_code_integration
# ------------------------------------------------------------------ #


class TestUninstallGlobal:
    def test_removes_skill_file(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        uninstall_claude_code_integration(global_install=True)
        skill_path = fake_home / ".claude" / "skills" / "engram" / "SKILL.md"
        assert not skill_path.exists()

    def test_removes_agent_file(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        uninstall_claude_code_integration(global_install=True)
        agent_path = fake_home / ".claude" / "agents" / "engram-reviewer.md"
        assert not agent_path.exists()

    def test_removes_engram_hooks(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        uninstall_claude_code_integration(global_install=True)
        settings_path = fake_home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        for event_hooks in settings.get("hooks", {}).values():
            for h in event_hooks:
                assert "engram" not in h.get("command", "")

    def test_preserves_other_hooks(self, fake_home: Path) -> None:
        # Pre-existing hook
        settings_dir = fake_home / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text(json.dumps({
            "hooks": {
                "Stop": [
                    {"type": "command", "command": "my-tool goodbye", "timeout": 2000}
                ]
            }
        }))
        install_claude_code_integration(global_install=True)
        uninstall_claude_code_integration(global_install=True)
        settings = json.loads((settings_dir / "settings.json").read_text())
        stop_cmds = [h["command"] for h in settings["hooks"]["Stop"]]
        assert "my-tool goodbye" in stop_cmds

    def test_preserves_engram_data(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        store_root = fake_home / ".claude" / "engrams"
        # Write a fake engram file
        (store_root / "engram" / "test.md").write_text("test data")
        uninstall_claude_code_integration(global_install=True)
        # Data should still be there
        assert (store_root / "engram" / "test.md").exists()

    def test_report_structure(self, fake_home: Path) -> None:
        install_claude_code_integration(global_install=True)
        report = uninstall_claude_code_integration(global_install=True)
        assert "removed" in report
        assert isinstance(report["removed"], list)
        assert len(report["removed"]) > 0

    def test_uninstall_when_not_installed(self, fake_home: Path) -> None:
        """Uninstall when nothing is installed should not crash."""
        report = uninstall_claude_code_integration(global_install=True)
        assert isinstance(report, dict)


class TestUninstallProject:
    def test_removes_skill_from_project(
        self, fake_home: Path, project_dir: Path
    ) -> None:
        install_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        uninstall_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        skill_path = project_dir / ".claude" / "skills" / "engram" / "SKILL.md"
        assert not skill_path.exists()

    def test_preserves_project_engram_data(
        self, fake_home: Path, project_dir: Path
    ) -> None:
        install_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        (project_dir / ".engram" / "engram" / "test.md").write_text("data")
        uninstall_claude_code_integration(
            global_install=False, project_path=project_dir
        )
        assert (project_dir / ".engram" / "engram" / "test.md").exists()


# ------------------------------------------------------------------ #
# CLI commands
# ------------------------------------------------------------------ #


class TestCLIInstall:
    def test_install_command(self, fake_home: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["install"])
        assert result.exit_code == 0
        assert "installed" in result.output.lower()

    def test_install_project_flag(
        self, fake_home: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(project_dir)
        runner = CliRunner()
        result = runner.invoke(main, ["install", "--project"])
        assert result.exit_code == 0
        assert "installed" in result.output.lower()


class TestCLIUninstall:
    def test_uninstall_command(self, fake_home: Path) -> None:
        runner = CliRunner()
        runner.invoke(main, ["install"])
        result = runner.invoke(main, ["uninstall"])
        assert result.exit_code == 0
        assert "uninstalled" in result.output.lower()

    def test_uninstall_preserves_data_message(self, fake_home: Path) -> None:
        runner = CliRunner()
        runner.invoke(main, ["install"])
        result = runner.invoke(main, ["uninstall"])
        assert "preserved" in result.output.lower()
