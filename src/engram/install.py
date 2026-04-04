"""Engram Claude Code integration — install and uninstall."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

# Source directory for integration files bundled with the package.
_PACKAGE_DIR = Path(__file__).parent
_CLAUDE_DIR = _PACKAGE_DIR / "claude"
_SKILL_SRC = _CLAUDE_DIR / "skills" / "engram" / "SKILL.md"
_AGENT_SRC = _CLAUDE_DIR / "agents" / "engram-reviewer.md"

HOOK_CONFIG: dict[str, dict[str, list[dict[str, object]]]] = {
    "hooks": {
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "engram review --from-hook --mode=auto",
                        "timeout": 30000,
                    },
                    {
                        "type": "command",
                        "command": (
                            "engram signal --event=session_end"
                            " --from-hook --slug=_session"
                        ),
                        "timeout": 1000,
                    },
                ],
            },
        ],
        "PostToolUse": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": (
                            "engram signal --event=tool_use"
                            " --from-hook --slug=_session"
                        ),
                        "timeout": 1000,
                    },
                ],
            },
        ],
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "engram select --from-hook",
                        "timeout": 2000,
                    },
                ],
            },
        ],
    }
}


def _merge_hooks(
    existing: dict[str, object],
    new_hooks: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    """Merge new hooks into existing settings without creating duplicates.

    *existing* is the full settings dict (may contain non-hook keys).
    *new_hooks* maps event names to lists of matcher objects (new format).
    Each matcher object has {"matcher": "...", "hooks": [...]}.

    Returns the updated settings dict (mutated in place for convenience).
    """
    if "hooks" not in existing:
        existing["hooks"] = {}
    hooks: dict[str, list[dict[str, object]]] = existing["hooks"]  # type: ignore[assignment]

    for event, new_matchers in new_hooks.items():
        current_matchers = hooks.get(event, [])

        for new_matcher in new_matchers:
            matcher_pattern = new_matcher.get("matcher", "")
            new_hook_list = new_matcher.get("hooks", [])
            if not isinstance(new_hook_list, list):
                continue

            # Find existing matcher with same pattern
            existing_matcher = None
            for m in current_matchers:
                if m.get("matcher") == matcher_pattern:
                    existing_matcher = m
                    break

            if existing_matcher:
                # Merge hooks into existing matcher
                existing_hook_list = existing_matcher.get("hooks", [])
                if not isinstance(existing_hook_list, list):
                    existing_hook_list = []
                    existing_matcher["hooks"] = existing_hook_list

                current_commands = {
                    h.get("command"): h
                    for h in existing_hook_list
                    if isinstance(h, dict)
                }
                for hook in new_hook_list:
                    if not isinstance(hook, dict):
                        continue
                    cmd = hook.get("command")
                    if cmd in current_commands:
                        # Update timeout for existing hooks on reinstall
                        if "timeout" in hook:
                            current_commands[cmd]["timeout"] = hook["timeout"]
                    else:
                        existing_hook_list.append(hook)
            else:
                # Add new matcher object
                current_matchers.append(new_matcher)

        hooks[event] = current_matchers

    return existing


def _remove_hooks(
    existing: dict[str, object],
    hooks_to_remove: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    """Remove engram hooks from settings, leaving other hooks intact.

    *existing* is the full settings dict.
    *hooks_to_remove* maps event names to lists of matcher objects (new format).
    Each matcher object has {"matcher": "...", "hooks": [...]}.
    Hooks are matched by command string.

    Returns the updated settings dict.
    """
    hooks: dict[str, list[dict[str, object]]] = existing.get("hooks", {})  # type: ignore[assignment]
    if not hooks:
        return existing

    for event, matchers_to_remove in hooks_to_remove.items():
        if event not in hooks:
            continue

        current_matchers = hooks[event]

        # Collect all commands to remove from all matcher objects
        commands_to_remove = set()
        for matcher in matchers_to_remove:
            hook_list = matcher.get("hooks", [])
            if isinstance(hook_list, list):
                for h in hook_list:
                    if isinstance(h, dict):
                        commands_to_remove.add(h.get("command"))

        # Remove commands from each matcher's hooks array
        updated_matchers = []
        for matcher in current_matchers:
            hook_list = matcher.get("hooks", [])
            if isinstance(hook_list, list):
                filtered_hooks = [
                    h for h in hook_list
                    if not isinstance(h, dict) or h.get("command") not in commands_to_remove
                ]
                # Only keep matcher if it still has hooks
                if filtered_hooks:
                    matcher["hooks"] = filtered_hooks
                    updated_matchers.append(matcher)
            else:
                updated_matchers.append(matcher)

        hooks[event] = updated_matchers

    existing["hooks"] = hooks
    return existing


def _read_settings(settings_path: Path) -> dict[str, object]:
    """Read a settings.json, returning empty dict if it doesn't exist."""
    if settings_path.exists():
        return json.loads(settings_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {}


def _write_settings(settings_path: Path, settings: dict[str, object]) -> None:
    """Write settings dict to JSON file, creating parent dirs as needed."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )


def install_claude_code_integration(
    global_install: bool = True,
    project_path: Path | None = None,
) -> dict[str, list[str]]:
    """Install Engram's Claude Code integration.

    1. Copy skill file to target .claude/skills/engram/SKILL.md
    2. Copy agent file to ~/.claude/agents/engram-reviewer.md (always global)
    3. Create engram store directories
    4. Merge hook configuration into settings.json (don't overwrite existing hooks)

    Returns dict with keys ``created`` and ``updated``, each a list of path strings.
    """
    created: list[str] = []
    updated: list[str] = []

    home_claude = Path.home() / ".claude"

    # Determine base dir for skill and settings
    if global_install:
        base_claude = home_claude
        store_root = home_claude / "engrams"
    else:
        if project_path is None:
            msg = "project_path is required for project-level install"
            raise ValueError(msg)
        base_claude = project_path / ".claude"
        store_root = project_path / ".engram"

    # 1. Copy skill file
    skill_dest = base_claude / "skills" / "engram" / "SKILL.md"
    skill_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_SKILL_SRC, skill_dest)
    created.append(str(skill_dest))

    # 2. Copy agent file (always global)
    agent_dest = home_claude / "agents" / "engram-reviewer.md"
    agent_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_AGENT_SRC, agent_dest)
    created.append(str(agent_dest))

    # 3. Create store directories
    for subdir in ("engram", "archive", "metrics", "versions"):
        d = store_root / subdir
        d.mkdir(parents=True, exist_ok=True)
    created.append(str(store_root))

    # 4. Merge hooks into settings.json
    settings_path = base_claude / "settings.json"
    settings = _read_settings(settings_path)
    _merge_hooks(settings, HOOK_CONFIG["hooks"])
    _write_settings(settings_path, settings)
    updated.append(str(settings_path))

    return {"created": created, "updated": updated}


def uninstall_claude_code_integration(
    global_install: bool = True,
    project_path: Path | None = None,
) -> dict[str, list[str]]:
    """Remove Engram's Claude Code integration WITHOUT deleting engram data.

    1. Remove skill file
    2. Remove agent file
    3. Remove engram hooks from settings.json (leave other hooks intact)
    4. Do NOT delete engram store

    Returns dict with key ``removed``, a list of path strings.
    """
    removed: list[str] = []

    home_claude = Path.home() / ".claude"

    if global_install:
        base_claude = home_claude
    else:
        if project_path is None:
            msg = "project_path is required for project-level uninstall"
            raise ValueError(msg)
        base_claude = project_path / ".claude"

    # 1. Remove skill file
    skill_dest = base_claude / "skills" / "engram" / "SKILL.md"
    if skill_dest.exists():
        skill_dest.unlink()
        removed.append(str(skill_dest))
        # Clean up empty parent dirs
        skill_dir = skill_dest.parent
        if skill_dir.exists() and not any(skill_dir.iterdir()):
            skill_dir.rmdir()

    # 2. Remove agent file
    agent_dest = home_claude / "agents" / "engram-reviewer.md"
    if agent_dest.exists():
        agent_dest.unlink()
        removed.append(str(agent_dest))

    # 3. Remove engram hooks from settings.json
    settings_path = base_claude / "settings.json"
    if settings_path.exists():
        settings = _read_settings(settings_path)
        _remove_hooks(settings, HOOK_CONFIG["hooks"])
        _write_settings(settings_path, settings)
        removed.append(str(settings_path) + " (hooks removed)")

    return {"removed": removed}
