"""Engram CLI — Click-based command interface."""

from __future__ import annotations

from pathlib import Path

import click

from engram import __version__
from engram.formatting import format_engram_detail, format_engram_table
from engram.models import EngramState
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
