"""Engram CLI — Click-based command interface."""

from __future__ import annotations

from pathlib import Path

import click
import frontmatter

from engram import __version__
from engram.evaluator import EngramEvaluator
from engram.formatting import format_engram_detail, format_engram_table
from engram.hooks import record_feedback, record_signal
from engram.lifecycle import LifecycleManager
from engram.models import Engram, EngramState, SessionContext
from engram.reviewer import EngramReviewer
from engram.scanner import EngramScanner
from engram.selector import EngramSelector
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


# ------------------------------------------------------------------
# Skill export command
# ------------------------------------------------------------------


@main.command("export-skill")
@click.argument("slug")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None,
              help="Write SKILL.md to file instead of stdout.")
@click.pass_context
def export_skill(ctx: click.Context, slug: str, output_path: Path | None) -> None:
    """Export an engram as a Claude Code SKILL.md file."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None

    reviewer = EngramReviewer(store)
    content = reviewer.render_skill_template(engram)

    if output_path is not None:
        output_path.write_text(content, encoding="utf-8")
        click.echo(f"Exported {slug} as SKILL.md to {output_path}")
    else:
        click.echo(content)


# ------------------------------------------------------------------
# Selector command
# ------------------------------------------------------------------


@main.command("select")
@click.option("--prompt", "prompt_text", default="", help="Prompt text to match against.")
@click.option("--project", "project_path", default=None, help="Project path.")
@click.option("--file", "files", multiple=True, help="File paths in context.")
@click.option("--tag", "tags", multiple=True, help="Context tags.")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None,
              help="Write injection output to file instead of stdout.")
@click.pass_context
def select_cmd(ctx: click.Context, prompt_text: str, project_path: str | None,
               files: tuple[str, ...], tags: tuple[str, ...],
               output_path: Path | None) -> None:
    """Select relevant engrams for the current context."""
    store = _get_store(ctx.obj["store_path"])
    context = SessionContext(
        project_path=project_path,
        files=list(files),
        tags=list(tags),
        prompt=prompt_text,
    )
    selector = EngramSelector(store)
    scored = selector.select(context)
    output = selector.format_injection(scored)

    if output_path is not None:
        output_path.write_text(output, encoding="utf-8")
        click.echo(f"Wrote {len(scored)} engram(s) to {output_path}")
    else:
        if output:
            click.echo(output)
        else:
            click.echo("No matching engrams found.")


# ------------------------------------------------------------------
# Lifecycle commands
# ------------------------------------------------------------------

_PROMOTE_MAP: dict[EngramState, EngramState] = {
    EngramState.DRAFT: EngramState.CANDIDATE,
    EngramState.CANDIDATE: EngramState.STABLE,
}


@main.command()
@click.argument("slug")
@click.pass_context
def promote(ctx: click.Context, slug: str) -> None:
    """Promote an engram to the next lifecycle state."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None

    target = _PROMOTE_MAP.get(engram.state)
    if target is None:
        raise click.ClickException(
            f"Cannot promote engram in state '{engram.state.value}'"
        ) from None

    scanner = EngramScanner()
    lm = LifecycleManager(store, scanner=scanner)
    try:
        result = lm.apply_transition(slug, target, "promoted via CLI")
    except ValueError as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"Promoted {slug} to {result.state.value}")


@main.command()
@click.argument("slug")
@click.pass_context
def deprecate(ctx: click.Context, slug: str) -> None:
    """Move an engram to deprecated state."""
    store = _get_store(ctx.obj["store_path"])
    lm = LifecycleManager(store)
    try:
        result = lm.apply_transition(slug, EngramState.DEPRECATED, "deprecated via CLI")
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"Deprecated {slug} (now {result.state.value})")


@main.command()
@click.argument("slug")
@click.pass_context
def archive(ctx: click.Context, slug: str) -> None:
    """Move an engram to archived state."""
    store = _get_store(ctx.obj["store_path"])
    lm = LifecycleManager(store)
    try:
        result = lm.apply_transition(slug, EngramState.ARCHIVED, "archived via CLI")
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"Archived {slug} (now {result.state.value})")


@main.command()
@click.argument("slug")
@click.pass_context
def demote(ctx: click.Context, slug: str) -> None:
    """Reset an engram to draft state for rework."""
    store = _get_store(ctx.obj["store_path"])
    lm = LifecycleManager(store)
    try:
        result = lm.apply_transition(slug, EngramState.DRAFT, "demoted via CLI")
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"Demoted {slug} to {result.state.value}")


@main.command()
@click.argument("slug")
@click.argument("version", type=int)
@click.pass_context
def rollback(ctx: click.Context, slug: str, version: int) -> None:
    """Rollback an engram to a previous version."""
    store = _get_store(ctx.obj["store_path"])
    lm = LifecycleManager(store)
    try:
        result = lm.rollback(slug, version)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"Rolled back {slug} to v{version} (now v{result.version})")


@main.command()
@click.pass_context
def gc(ctx: click.Context) -> None:
    """Run garbage collection."""
    store = _get_store(ctx.obj["store_path"])
    lm = LifecycleManager(store)
    report = lm.run_gc()
    if report.archived:
        click.echo(f"Archived: {', '.join(report.archived)}")
    if report.orphan_metrics_cleaned:
        click.echo(f"Orphan metrics cleaned: {', '.join(report.orphan_metrics_cleaned)}")
    if report.orphan_versions_cleaned:
        click.echo(f"Orphan versions cleaned: {', '.join(report.orphan_versions_cleaned)}")
    total = (
        len(report.archived)
        + len(report.orphan_metrics_cleaned)
        + len(report.orphan_versions_cleaned)
    )
    click.echo(f"GC complete: {total} item(s) cleaned")


@main.command()
@click.argument("slug")
@click.pass_context
def dedup(ctx: click.Context, slug: str) -> None:
    """Show deduplication candidates for an engram."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None

    lm = LifecycleManager(store)
    candidates = lm.check_duplicates(engram)
    if not candidates:
        click.echo(f"No duplicates found for {slug}")
        return
    click.echo(f"Potential duplicates for {slug}:")
    for c in candidates:
        click.echo(
            f"  {c.slug} ({c.similarity_type}: {c.similarity_score:.2f}) "
            f"— {c.description}"
        )


# ------------------------------------------------------------------
# Reviewer command
# ------------------------------------------------------------------


@main.command()
@click.option("--session", "session_id", default=None, help="Session ID to review.")
@click.option("--mode", type=click.Choice(["auto", "interactive"]), default="auto")
@click.pass_context
def review(ctx: click.Context, session_id: str | None, mode: str) -> None:
    """Review a session for procedural knowledge to capture as engrams."""
    store = _get_store(ctx.obj["store_path"])
    scanner = EngramScanner()
    reviewer = EngramReviewer(store, scanner=scanner)

    # Build context (minimal for CLI — full context comes from hooks)
    session_ctx = {
        "project_path": str(Path.cwd()),
        "session_id": session_id or "cli-review",
        "tool_calls": [],
        "outcome": "unknown",
    }

    prompt = reviewer.build_review_prompt(session_ctx)
    click.echo(f"Review prompt built ({len(prompt)} chars)")
    click.echo(f"Mode: {mode}")
    if mode == "interactive":
        click.echo("Interactive review requires Claude Code agent. Use /engram review instead.")
    else:
        click.echo("Auto review requires session transcript. No actions taken.")


# ------------------------------------------------------------------
# Install / uninstall commands
# ------------------------------------------------------------------


@main.command()
@click.option("--global/--project", "global_install", default=True,
              help="Install globally or for current project.")
@click.pass_context
def install(ctx: click.Context, global_install: bool) -> None:
    """Install Claude Code integration (skill, agent, hooks)."""
    from engram.install import install_claude_code_integration

    project_path = Path.cwd() if not global_install else None
    report = install_claude_code_integration(
        global_install=global_install,
        project_path=project_path,
    )
    for path in report.get("created", []):
        click.echo(f"  Created: {path}")
    for path in report.get("updated", []):
        click.echo(f"  Updated: {path}")
    click.echo("Engram integration installed.")


@main.command()
@click.option("--global/--project", "global_install", default=True,
              help="Uninstall globally or for current project.")
@click.pass_context
def uninstall(ctx: click.Context, global_install: bool) -> None:
    """Remove Claude Code integration (preserves engram data)."""
    from engram.install import uninstall_claude_code_integration

    project_path = Path.cwd() if not global_install else None
    report = uninstall_claude_code_integration(
        global_install=global_install,
        project_path=project_path,
    )
    for path in report.get("removed", []):
        click.echo(f"  Removed: {path}")
    click.echo("Engram integration uninstalled. Engram data preserved.")


# ------------------------------------------------------------------
# Import / export commands
# ------------------------------------------------------------------


@main.command("export")
@click.argument("slug")
@click.option(
    "--output", "output_path", type=click.Path(path_type=Path), default=None,
    help="Write to file instead of stdout.",
)
@click.pass_context
def export_cmd(ctx: click.Context, slug: str, output_path: Path | None) -> None:
    """Export an engram to a standalone Markdown file."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None

    meta = engram.model_dump(mode="json", exclude={"body"})
    post = frontmatter.Post(engram.body, **meta)
    content = frontmatter.dumps(post)

    if output_path is not None:
        output_path.write_text(content, encoding="utf-8")
        click.echo(f"Exported {slug} to {output_path}")
    else:
        click.echo(content)


@main.command("import")
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def import_cmd(ctx: click.Context, file_path: Path) -> None:
    """Import an engram from a file. Full security scan applied."""
    store = _get_store(ctx.obj["store_path"])

    # Parse file
    try:
        post = frontmatter.load(str(file_path))
        meta = dict(post.metadata)
        meta["body"] = post.content
        # Force draft state for safety
        meta["state"] = EngramState.DRAFT.value
        engram = Engram.model_validate(meta)
    except Exception as e:
        raise click.ClickException(f"Failed to parse engram file: {e}") from None

    # Security scan
    scanner = EngramScanner()
    verdict = scanner.scan(engram)

    # Print scan findings
    for finding in verdict.results:
        click.echo(
            f"  [{finding.severity.upper()}] {finding.pattern_id}: "
            f"{finding.message} (line {finding.line_number})"
        )

    if verdict.action == "block":
        click.echo(f"\nScan verdict: BLOCK — {engram.name} was not imported.")
        ctx.exit(1)
        return

    if verdict.action == "warn":
        click.echo("\nScan verdict: WARNING — proceeding with import.")

    # Write to store
    store.write(engram)
    click.echo(f"Imported {engram.name} (state=draft)")
