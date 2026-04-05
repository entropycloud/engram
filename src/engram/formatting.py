"""CLI output formatting helpers."""

from __future__ import annotations

from engram.models import Engram, IndexEntry


# Column definitions: (header, alignment, extract_fn)
# alignment: "<" = left, ">" = right
_COLUMNS = [
    ("Name", "<", lambda slug, entry, loc: slug),
    ("Location", "<", lambda slug, entry, loc: loc or ""),
    ("State", "<", lambda slug, entry, loc: entry.state.value),
    ("Trust", "<", lambda slug, entry, loc: entry.trust.value),
    ("Score", ">", lambda slug, entry, loc: f"{entry.quality_score:.2f}"),
    ("Ver", ">", lambda slug, entry, loc: str(entry.version)),
]


def _build_table(
    rows: list[tuple[str, IndexEntry, str | None]],
    show_location: bool,
) -> str:
    """Build a formatted table from rows of (slug, entry, location)."""
    if not rows:
        return "No engrams found."

    # Select which columns to show
    cols = [c for c in _COLUMNS if show_location or c[0] != "Location"]

    # Compute column widths from data
    widths = [len(c[0]) for c in cols]
    cell_data: list[list[str]] = []
    for slug, entry, loc in rows:
        cells = [extract(slug, entry, loc) for _, _, extract in cols]
        cell_data.append(cells)
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))

    # Add padding between columns
    pad = 2

    # Build header
    header_parts = []
    for i, (name, align, _) in enumerate(cols):
        if align == ">":
            header_parts.append(name.rjust(widths[i]))
        else:
            header_parts.append(name.ljust(widths[i]))
    header = (" " * pad).join(header_parts)
    sep = "-" * len(header)

    # Build rows
    lines = [header, sep]
    for cells in cell_data:
        parts = []
        for i, cell in enumerate(cells):
            align = cols[i][1]
            if align == ">":
                parts.append(cell.rjust(widths[i]))
            else:
                parts.append(cell.ljust(widths[i]))
        lines.append((" " * pad).join(parts))

    lines.append(f"\n{len(rows)} engram(s)")
    return "\n".join(lines)


def format_engram_table(
    engrams: dict[str, IndexEntry],
    location: str | None = None,
) -> str:
    """Format engrams as a human-readable table for CLI output."""
    rows = [(slug, entry, location) for slug, entry in sorted(engrams.items())]
    return _build_table(rows, show_location=location is not None)


def format_engram_table_multi(
    sections: list[tuple[str, dict[str, IndexEntry]]],
) -> str:
    """Format engrams from multiple stores with a Location column."""
    rows: list[tuple[str, IndexEntry, str | None]] = []
    for label, engrams in sections:
        for slug, entry in sorted(engrams.items()):
            rows.append((slug, entry, label))
    return _build_table(rows, show_location=True)


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
