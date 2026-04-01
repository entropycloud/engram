"""Fuzzy patch engine — apply targeted patches to engrams."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from engram.models import Engram, Triggers


class PatchType(StrEnum):
    """Types of patches that can be applied to an engram."""

    APPEND = "append"
    REPLACE_SECTION = "replace_section"
    FRONTMATTER_MERGE = "frontmatter_merge"
    FULL_REWRITE = "full_rewrite"


@dataclass
class Patch:
    """A targeted patch to apply to an engram."""

    patch_type: PatchType
    content: str = ""
    section_heading: str = ""
    frontmatter_updates: dict[str, Any] = field(default_factory=dict)


# Regex: a line starting with one or more # followed by a space
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def find_section(body: str, heading: str) -> tuple[int, int] | None:
    """Find the start and end character positions of a markdown section.

    A section starts at the heading line and ends at the next heading
    of the same or higher level (fewer or equal # characters), or end of document.
    Returns (start, end) or None if not found.
    """
    # Parse the target heading to extract level and text
    target_match = re.match(r"^(#{1,6})\s+(.+)$", heading.strip())
    if target_match is None:
        return None

    target_level = len(target_match.group(1))
    target_text = target_match.group(2).strip().lower()

    # Find all headings in the body
    for match in _HEADING_RE.finditer(body):
        level = len(match.group(1))
        text = match.group(2).strip().lower()

        if level == target_level and text == target_text:
            start = match.start()
            # Search for the next heading of same or higher level
            end = _find_section_end(body, start + len(match.group(0)), target_level)
            return (start, end)

    return None


def _find_section_end(body: str, search_from: int, heading_level: int) -> int:
    """Find where a section ends: at the next same-or-higher-level heading, or EOF."""
    for match in _HEADING_RE.finditer(body, search_from):
        level = len(match.group(1))
        if level <= heading_level:
            return match.start()
    return len(body)


def merge_triggers(existing: Triggers, updates: dict[str, Any]) -> Triggers:
    """Merge trigger updates into existing triggers using union for lists."""
    merged = existing.model_copy(deep=True)

    for list_field in ("tags", "patterns", "projects", "files"):
        if list_field in updates:
            current: list[str] = getattr(merged, list_field)
            new_values: list[str] = updates[list_field]
            # Union: preserve order, add new items at end
            seen = set(current)
            combined = list(current)
            for val in new_values:
                if val not in seen:
                    combined.append(val)
                    seen.add(val)
            setattr(merged, list_field, combined)

    return merged


def apply_patch(engram: Engram, patch: Patch) -> Engram:
    """Apply a patch to an engram, returning a new Engram with the changes.

    Does NOT modify the input engram. Returns a new Engram instance.
    Bumps version for frontmatter_merge and full_rewrite.
    Updates the 'updated' timestamp.
    """
    now = datetime.now(tz=UTC)

    if patch.patch_type == PatchType.APPEND:
        return _apply_append(engram, patch, now)
    elif patch.patch_type == PatchType.REPLACE_SECTION:
        return _apply_replace_section(engram, patch, now)
    elif patch.patch_type == PatchType.FRONTMATTER_MERGE:
        return _apply_frontmatter_merge(engram, patch, now)
    elif patch.patch_type == PatchType.FULL_REWRITE:
        return _apply_full_rewrite(engram, patch, now)
    else:
        raise ValueError(f"Unknown patch type: {patch.patch_type}")


def _apply_append(engram: Engram, patch: Patch, now: datetime) -> Engram:
    """Append content at the end of the body."""
    body = engram.body
    if body and not body.endswith("\n"):
        body += "\n"
    body += patch.content
    return engram.model_copy(update={"body": body, "updated": now})


def _apply_replace_section(engram: Engram, patch: Patch, now: datetime) -> Engram:
    """Replace a named markdown section in the body."""
    section = find_section(engram.body, patch.section_heading)
    if section is None:
        # Section not found — return copy with updated timestamp only
        return engram.model_copy(update={"updated": now})

    start, end = section
    new_body = engram.body[:start] + patch.content + engram.body[end:]
    return engram.model_copy(update={"body": new_body, "updated": now})


def _apply_frontmatter_merge(engram: Engram, patch: Patch, now: datetime) -> Engram:
    """Merge new values into existing frontmatter fields."""
    updates: dict[str, Any] = {}

    for key, value in patch.frontmatter_updates.items():
        if key == "triggers" and isinstance(value, dict):
            updates["triggers"] = merge_triggers(engram.triggers, value)
        else:
            updates[key] = value

    updates["version"] = engram.version + 1
    updates["updated"] = now

    return engram.model_copy(update=updates)


def _apply_full_rewrite(engram: Engram, patch: Patch, now: datetime) -> Engram:
    """Replace the entire body, preserving frontmatter except version + updated."""
    return engram.model_copy(
        update={
            "body": patch.content,
            "version": engram.version + 1,
            "updated": now,
        }
    )
