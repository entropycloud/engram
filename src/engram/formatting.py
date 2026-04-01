"""CLI output formatting helpers."""

from __future__ import annotations

from engram.models import Engram, IndexEntry


def format_engram_table(engrams: dict[str, IndexEntry]) -> str:
    """Format engrams as a human-readable table for CLI output."""
    if not engrams:
        return "No engrams found."

    # Column headers
    lines = [
        f"{'Name':<35} {'State':<12} {'Trust':<15} {'Score':>6} {'Ver':>4}",
        "-" * 76,
    ]

    for slug, entry in sorted(engrams.items()):
        lines.append(
            f"{slug:<35} {entry.state.value:<12} {entry.trust.value:<15} "
            f"{entry.quality_score:>5.2f} {entry.version:>4}"
        )

    lines.append(f"\n{len(engrams)} engram(s)")
    return "\n".join(lines)


def format_engram_detail(engram: Engram) -> str:
    """Format a single engram for detailed CLI view."""
    lines = [
        f"# {engram.name} (v{engram.version})",
        "",
        f"  Description:  {engram.description}",
        f"  State:        {engram.state.value}",
        f"  Trust:        {engram.trust.value}",
        f"  Created:      {engram.created.isoformat()}",
        f"  Updated:      {engram.updated.isoformat()}",
    ]

    if engram.supersedes:
        lines.append(f"  Supersedes:   {engram.supersedes}")
    if engram.superseded_by:
        lines.append(f"  Superseded by: {engram.superseded_by}")

    # Triggers
    if engram.triggers.tags:
        lines.append(f"  Tags:         {', '.join(engram.triggers.tags)}")
    if engram.triggers.patterns:
        lines.append(f"  Patterns:     {', '.join(engram.triggers.patterns)}")
    if engram.triggers.projects:
        lines.append(f"  Projects:     {', '.join(engram.triggers.projects)}")
    if engram.triggers.files:
        lines.append(f"  Files:        {', '.join(engram.triggers.files)}")

    # Tools
    if engram.allowed_tools:
        lines.append(f"  Allowed tools: {', '.join(engram.allowed_tools)}")

    # Metrics
    m = engram.metrics
    lines.extend([
        "",
        "  Metrics:",
        f"    Quality:    {m.quality_score:.3f}",
        f"    Usage:      {m.usage_count} ({m.success_count} success, {m.override_count} override)",
        f"    Streak:     {m.streak}",
    ])
    if m.last_used:
        lines.append(f"    Last used:  {m.last_used.isoformat()}")

    # Lineage
    ln = engram.lineage
    if ln.created_from or ln.parent or ln.creation_reason:
        lines.append("")
        lines.append("  Lineage:")
        if ln.parent:
            lines.append(f"    Parent:     {ln.parent}")
        if ln.created_from:
            lines.append(f"    From:       {ln.created_from}")
        if ln.creation_reason:
            lines.append(f"    Reason:     {ln.creation_reason}")

    # Body
    lines.extend(["", "---", "", engram.body])

    return "\n".join(lines)
