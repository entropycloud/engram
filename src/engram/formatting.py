"""CLI output formatting helpers."""

from __future__ import annotations

from engram.models import Engram, IndexEntry


def format_engram_table(
    engrams: dict[str, IndexEntry],
    location: str | None = None,
) -> str:
    """Format engrams as a human-readable table for CLI output.

    If *location* is provided, a Location column is added to each row.
    """
    if not engrams:
        return "No engrams found."

    show_loc = location is not None

    if show_loc:
        header = (
            f"{'Name':<35} {'Location':<9} {'State':<12} "
            f"{'Trust':<15} {'Score':>6} {'Ver':>4}"
        )
        sep = "-" * 86
    else:
        header = (
            f"{'Name':<35} {'State':<12} {'Trust':<15} {'Score':>6} {'Ver':>4}"
        )
        sep = "-" * 76

    lines = [header, sep]

    for slug, entry in sorted(engrams.items()):
        if show_loc:
            lines.append(
                f"{slug:<35} {location:<9} {entry.state.value:<12} "
                f"{entry.trust.value:<15} {entry.quality_score:>5.2f} "
                f"{entry.version:>4}"
            )
        else:
            lines.append(
                f"{slug:<35} {entry.state.value:<12} {entry.trust.value:<15} "
                f"{entry.quality_score:>5.2f} {entry.version:>4}"
            )

    lines.append(f"\n{len(engrams)} engram(s)")
    return "\n".join(lines)


def format_engram_table_multi(
    sections: list[tuple[str, dict[str, IndexEntry]]],
) -> str:
    """Format engrams from multiple stores with a Location column.

    *sections* is a list of (location_label, engrams_dict) tuples.
    """
    total = 0
    header = (
        f"{'Name':<35} {'Location':<9} {'State':<12} "
        f"{'Trust':<15} {'Score':>6} {'Ver':>4}"
    )
    lines = [header, "-" * 86]

    for label, engrams in sections:
        for slug, entry in sorted(engrams.items()):
            lines.append(
                f"{slug:<35} {label:<9} {entry.state.value:<12} "
                f"{entry.trust.value:<15} {entry.quality_score:>5.2f} "
                f"{entry.version:>4}"
            )
            total += 1

    if total == 0:
        return "No engrams found."

    lines.append(f"\n{total} engram(s)")
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
