"""Engram CLI — Click-based command interface."""

from __future__ import annotations

from pathlib import Path

import click

from engram import __version__
from engram.evaluator import EngramEvaluator
from engram.formatting import format_engram_detail, format_engram_table
from engram.hooks import record_feedback, record_signal
from engram.models import EngramState
from engram.scanner import EngramScanner
from engram.store import EngramStore

# Default store paths
GLOBAL_STORE_PATH = Path.home() / ".claude" / "engrams"
PROJECT_STORE_PATH = Path.cwd() / ".engram"


def _get_store(store_path: Path | None = None) -> EngramStore:
    """Get an EngramStore, creating directories if needed."""
    path = store_path or GLOBAL_STORE_PATH
    for subdir in ("engram", "archive", "metrics", "versions"):
        (path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(path)


@click.group()
@click.version_option(version=__version__, prog_name="engram")
@click.option(
    "--store", "store_path", type=click.Path(path_type=Path), default=None,
    help="Override engram store path.",
)
@click.pass_context
def main(ctx: click.Context, store_path: Path | None) -> None:
    """Self-improving procedural memory for Claude Code."""
    ctx.ensure_object(dict)
    ctx.obj["store_path"] = store_path


@main.command("list")
@click.option("--state", type=click.Choice([s.value for s in EngramState]), default=None)
@click.option("--tag", default=None, help="Filter by tag.")
@click.pass_context
def list_cmd(ctx: click.Context, state: str | None, tag: str | None) -> None:
    """List engrams with optional filters."""
    store = _get_store(ctx.obj["store_path"])
    index = store.read_index()
    engrams = dict(index.engrams)

    if state:
        engrams = {k: v for k, v in engrams.items() if v.state.value == state}

    if tag:
        engrams = {k: v for k, v in engrams.items() if tag in v.tags}

    click.echo(format_engram_table(engrams))


@main.command()
@click.argument("slug")
@click.pass_context
def view(ctx: click.Context, slug: str) -> None:
    """Display the full engram with rendered frontmatter."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None
    click.echo(format_engram_detail(engram))


@main.command("rebuild-index")
@click.pass_context
def rebuild_index(ctx: click.Context) -> None:
    """Force rebuild of index.json from engram files."""
    store = _get_store(ctx.obj["store_path"])
    index = store.rebuild_index()
    click.echo(f"Index rebuilt: {len(index.engrams)} engram(s)")


@main.command()
@click.option("--slug", default=None, help="Show stats for a specific engram.")
@click.pass_context
def stats(ctx: click.Context, slug: str | None) -> None:
    """Show quality metrics and usage stats."""
    store = _get_store(ctx.obj["store_path"])
    evaluator = EngramEvaluator(store)

    if slug:
        try:
            engram = store.read(slug)
        except FileNotFoundError:
            raise click.ClickException(f"Engram not found: {slug}") from None
        events = evaluator.read_events(slug)
        m = engram.metrics
        click.echo(f"# {slug}")
        click.echo(f"  Quality score: {m.quality_score:.3f}")
        click.echo(
            f"  Usage: {m.usage_count} "
            f"(success: {m.success_count}, override: {m.override_count})"
        )
        click.echo(f"  Streak: {m.streak}")
        click.echo(f"  Events recorded: {len(events)}")
        if m.last_used:
            click.echo(f"  Last used: {m.last_used.isoformat()}")
    else:
        index = store.read_index()
        if not index.engrams:
            click.echo("No engrams found.")
            return
        lines = [
            f"{'Name':<35} {'Score':>6} {'Usage':>6} {'Success':>8} {'Override':>9}",
            "-" * 70,
        ]
        for name, _entry in sorted(index.engrams.items()):
            engram = store.read(name)
            m = engram.metrics
            lines.append(
                f"{name:<35} {m.quality_score:>5.3f} {m.usage_count:>6} "
                f"{m.success_count:>8} {m.override_count:>9}"
            )
        lines.append(f"\n{len(index.engrams)} engram(s)")
        click.echo("\n".join(lines))


@main.command()
@click.argument("slug")
@click.argument("rating", type=click.Choice(["up", "down"]))
@click.pass_context
def rate(ctx: click.Context, slug: str, rating: str) -> None:
    """Record explicit feedback (up/down) for an engram and update its score."""
    store = _get_store(ctx.obj["store_path"])
    try:
        store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None

    record_feedback(store.root, slug, "cli", rating)
    evaluator = EngramEvaluator(store)
    new_score = evaluator.update_engram_score(slug)
    click.echo(f"Recorded feedback '{rating}' for {slug}. New score: {new_score:.3f}")


@main.command()
@click.option("--event", "event_type", required=True,
              type=click.Choice(["used", "success", "override", "feedback"]),
              help="Event type to record.")
@click.option("--session", required=True, help="Session ID.")
@click.option("--slug", required=True, help="Engram slug.")
@click.option("--context", default=None, help="Context string.")
@click.option("--detail", default=None, help="Detail string.")
@click.pass_context
def signal(ctx: click.Context, event_type: str, session: str, slug: str,
           context: str | None, detail: str | None) -> None:
    """Record a signal (used by hooks)."""
    store = _get_store(ctx.obj["store_path"])
    record_signal(store.root, slug, event_type, session, context=context, detail=detail)
    click.echo(f"Recorded {event_type} for {slug}")


@main.command()
@click.argument("slug")
@click.pass_context
def scan(ctx: click.Context, slug: str) -> None:
    """Run security scan on an engram. Exit 0=allow, 1=block."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None

    scanner = EngramScanner()
    verdict = scanner.scan(engram)

    # Print results
    for result in verdict.results:
        click.echo(
            f"  [{result.severity.upper()}] {result.pattern_id}: "
            f"{result.message} (line {result.line_number})"
        )

    click.echo(f"\nVerdict: {verdict.action.upper()}")

    if verdict.action == "block":
        ctx.exit(1)
