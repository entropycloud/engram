"""Engram CLI — Click-based command interface."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click
import frontmatter

from engram import __version__
from engram.evaluator import EngramEvaluator
from engram.formatting import format_engram_detail, format_engram_table, format_engram_table_multi
from engram.hooks import record_feedback, record_signal
from engram.lifecycle import LifecycleManager
from engram.models import Engram, EngramState, SessionContext
from engram.reviewer import EngramReviewer
from engram.scanner import EngramScanner
from engram.selector import EngramSelector
from engram.store import EngramStore

# Default global store path (used only when no project store is found)
GLOBAL_STORE_PATH = Path.home() / ".claude" / "engrams"


def _find_project_store() -> Path | None:
    """Walk up from cwd looking for a .engram/ directory."""
    current = Path.cwd()
    for parent in (current, *current.parents):
        candidate = parent / ".engram"
        if candidate.is_dir():
            return candidate
    return None


def _get_store(store_path: Path | None = None) -> EngramStore:
    """Get an EngramStore with project-local auto-detection.

    Resolution order:
    1. Explicit ``store_path`` argument (from --store flag)
    2. ``.engram/`` found by walking up from cwd
    3. Fall back to ``~/.claude/engrams`` (global)
    """
    if store_path is None:
        store_path = _find_project_store() or GLOBAL_STORE_PATH
    for subdir in ("engram", "archive", "metrics", "versions"):
        (store_path / subdir).mkdir(parents=True, exist_ok=True)
    return EngramStore(store_path)


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

    def _filter(engrams: dict[str, object], state: str | None, tag: str | None) -> dict[str, object]:  # type: ignore[type-arg]
        if state:
            engrams = {k: v for k, v in engrams.items() if v.state.value == state}  # type: ignore[union-attr]
        if tag:
            engrams = {k: v for k, v in engrams.items() if tag in v.tags}  # type: ignore[union-attr]
        return engrams

    explicit_store = ctx.obj["store_path"]
    project_store_path = _find_project_store()

    if explicit_store:
        # Explicit --store: show just that store, no location column
        store = _get_store(explicit_store)
        engrams = _filter(dict(store.read_index().engrams), state, tag)
        click.echo(format_engram_table(engrams))  # type: ignore[arg-type]
    elif project_store_path:
        # In a project: show both project and global
        sections: list[tuple[str, dict]] = []  # type: ignore[type-arg]

        proj_store = EngramStore(project_store_path)
        proj_engrams = _filter(dict(proj_store.read_index().engrams), state, tag)
        sections.append(("project", proj_engrams))  # type: ignore[arg-type]

        if GLOBAL_STORE_PATH.exists():
            glob_store = EngramStore(GLOBAL_STORE_PATH)
            glob_engrams = _filter(dict(glob_store.read_index().engrams), state, tag)
            if glob_engrams:
                sections.append(("global", glob_engrams))  # type: ignore[arg-type]

        click.echo(format_engram_table_multi(sections))  # type: ignore[arg-type]
    else:
        # No project store: show global only, no location column
        store = _get_store(None)
        engrams = _filter(dict(store.read_index().engrams), state, tag)
        click.echo(format_engram_table(engrams))  # type: ignore[arg-type]


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
@click.option(
    "--event", "event_type", required=True,
    type=click.Choice([
        "used", "success", "override",
        "feedback", "session_end", "tool_use",
    ]),
    help="Event type to record.",
)
@click.option("--session", default=None, help="Session ID.")
@click.option("--slug", required=True, help="Engram slug.")
@click.option("--context", default=None, help="Context string.")
@click.option("--detail", default=None, help="Detail string.")
@click.option("--from-hook", is_flag=True, default=False,
              help="Read session ID from Claude Code hook JSON on stdin.")
@click.pass_context
def signal(ctx: click.Context, event_type: str, session: str | None, slug: str,
           context: str | None, detail: str | None, from_hook: bool) -> None:
    """Record a signal (used by hooks)."""
    if from_hook:
        import json
        import sys
        try:
            hook_data = json.load(sys.stdin)
            if not session:
                session = hook_data.get("session_id", "")
        except (json.JSONDecodeError, OSError):
            pass
    session = session or ""
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
@click.option("--prompt-file", "prompt_file", type=click.Path(path_type=Path, exists=True),
              default=None, help="Read prompt text from a file.")
@click.option("--from-hook", "from_hook", is_flag=True, default=False,
              help="Read hook JSON from stdin (UserPromptSubmit provides prompt via stdin).")
@click.option("--project", "project_path", default=None, help="Project path.")
@click.option("--file", "files", multiple=True, help="File paths in context.")
@click.option("--tag", "tags", multiple=True, help="Context tags.")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None,
              help="Write injection output to file instead of stdout.")
@click.pass_context
def select_cmd(ctx: click.Context, prompt_text: str, prompt_file: Path | None,
               from_hook: bool, project_path: str | None,
               files: tuple[str, ...], tags: tuple[str, ...],
               output_path: Path | None) -> None:
    """Select relevant engrams for the current context."""
    # --from-hook: read JSON from stdin (Claude Code UserPromptSubmit hook format)
    if from_hook:
        import json
        import sys
        try:
            hook_data = json.load(sys.stdin)
            prompt_text = hook_data.get("prompt", "")
            if not project_path:
                project_path = hook_data.get("cwd")
        except (json.JSONDecodeError, OSError):
            prompt_text = ""
    # --prompt-file takes precedence over --prompt
    elif prompt_file is not None:
        prompt_text = prompt_file.read_text(encoding="utf-8").strip()
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

    # Record injection tracking and "used" signals for hook mode
    if from_hook and scored:
        from engram.hooks import record_injection, record_signal

        session_id = hook_data.get("session_id", "") if from_hook else ""
        if session_id:
            injected_slugs = [s.slug for s in scored]
            record_injection(store.root, session_id, injected_slugs)
            for slug in injected_slugs:
                record_signal(
                    store.root, slug, "used", session_id,
                )

    # Check for completed background reviews and notify user
    if from_hook:
        review_summary = _check_pending_reviews(store.root)
        if review_summary:
            click.echo(review_summary)

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
@click.pass_context
def pin(ctx: click.Context, slug: str) -> None:
    """Pin an engram to prevent staleness decay and archival."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None
    engram.pinned = True
    engram.updated = datetime.now(tz=UTC)
    store.write(engram)
    click.echo(f"Pinned {slug}")


@main.command()
@click.argument("slug")
@click.pass_context
def unpin(ctx: click.Context, slug: str) -> None:
    """Unpin an engram, allowing normal staleness decay."""
    store = _get_store(ctx.obj["store_path"])
    try:
        engram = store.read(slug)
    except FileNotFoundError:
        raise click.ClickException(f"Engram not found: {slug}") from None
    engram.pinned = False
    engram.updated = datetime.now(tz=UTC)
    store.write(engram)
    click.echo(f"Unpinned {slug}")


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
@click.option("--transcript", "transcript_path", type=click.Path(path_type=Path),
              default=None, help="Path to session JSONL transcript file.")
@click.option("--mode", type=click.Choice(["auto", "interactive"]), default="auto")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the review prompt without calling the LLM.")
@click.option("--model", default=None, help="Override LLM model name.")
@click.option("--from-hook", is_flag=True, default=False,
              help="Read session ID from Claude Code hook JSON on stdin.")
@click.option("--background", is_flag=True, default=False,
              help="Fork review into a background process and exit immediately.")
@click.pass_context
def review(ctx: click.Context, session_id: str | None, transcript_path: Path | None,
           mode: str, dry_run: bool, model: str | None, from_hook: bool,
           background: bool) -> None:
    """Review a session for procedural knowledge to capture as engrams."""
    hook_data: dict[str, object] = {}
    if from_hook:
        import json
        import sys
        try:
            hook_data = json.load(sys.stdin)
            if not session_id:
                session_id = hook_data.get("session_id")  # type: ignore[assignment]
            if not transcript_path:
                tp = hook_data.get("transcript_path")
                if tp:
                    transcript_path = Path(str(tp))
        except (json.JSONDecodeError, OSError):
            pass

    # Background mode: fork a child process and exit immediately
    if background:
        _fork_background_review(
            session_id=session_id,
            transcript_path=transcript_path,
            model=model,
            store_path=ctx.obj["store_path"],
        )
        return

    _run_review(
        session_id=session_id,
        transcript_path=transcript_path,
        mode=mode,
        dry_run=dry_run,
        model=model,
        store_path=ctx.obj["store_path"],
    )


def _fork_background_review(
    session_id: str | None,
    transcript_path: Path | None,
    model: str | None,
    store_path: Path | None,
) -> None:
    """Fork a background process to run the review, then exit immediately."""
    import shutil
    import subprocess
    import sys

    # Prefer the same Python that's running us (preserves the venv with anthropic)
    engram_bin = shutil.which("engram")
    if engram_bin:
        cmd = [engram_bin, "review", "--mode=auto"]
    else:
        cmd = [sys.executable, "-m", "engram.cli", "review", "--mode=auto"]
    if session_id:
        cmd.extend(["--session", session_id])
    if transcript_path:
        cmd.extend(["--transcript", str(transcript_path)])
    if model:
        cmd.extend(["--model", model])
    if store_path:
        cmd.extend(["--store", str(store_path)])

    # Resolve the store for writing the review log
    store = _get_store(store_path)
    reviews_dir = store.root / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    log_path = reviews_dir / f"{session_id or 'unknown'}.log"

    log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    subprocess.Popen(  # noqa: S603
        cmd,
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    click.echo(f"Engram review started in background (log: {log_path})")


def _run_review(
    session_id: str | None,
    transcript_path: Path | None,
    mode: str,
    dry_run: bool,
    model: str | None,
    store_path: Path | None,
) -> None:
    """Run the review synchronously (used by both foreground and background)."""
    store = _get_store(store_path)
    scanner = EngramScanner()
    reviewer_obj = EngramReviewer(store, scanner=scanner)

    sid = session_id or "cli-review"

    # Try to find transcript if not explicitly provided
    if transcript_path is None and session_id:
        for projects_dir in (Path.home() / ".claude" / "projects",):
            if projects_dir.exists():
                for candidate in projects_dir.rglob(f"{session_id}.jsonl"):
                    transcript_path = candidate
                    break

    # Load injected engram slugs for this session
    from engram.hooks import cleanup_session_file, read_session_injections

    injected_slugs = read_session_injections(store.root, sid) if sid else []

    if transcript_path and transcript_path.exists():
        session_ctx = reviewer_obj.build_context_from_transcript(
            transcript_path,
            project_path=str(Path.cwd()),
            session_id=sid,
        )
        session_ctx["injected_slugs"] = injected_slugs
        prompt = reviewer_obj.build_review_prompt(session_ctx)
        tool_calls = session_ctx.get("tool_calls", [])
        tool_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        click.echo(f"Loaded transcript: {tool_count} tool call(s)")
        if injected_slugs:
            click.echo(f"Injected engrams: {', '.join(injected_slugs)}")
        click.echo(f"Review prompt built ({len(prompt)} chars)")
    else:
        session_ctx = {
            "project_path": str(Path.cwd()),
            "session_id": sid,
            "tool_calls": [],
            "outcome": "unknown",
            "injected_slugs": injected_slugs,
        }
        prompt = reviewer_obj.build_review_prompt(session_ctx)
        click.echo(f"Review prompt built ({len(prompt)} chars)")

    if dry_run:
        click.echo(prompt)
        return

    if mode == "interactive":
        click.echo("Interactive review requires Claude Code agent. Use /engram review instead.")
        return

    # Auto mode: call LLM, parse output, execute decisions
    from engram.llm import LLMError, call_reviewer_llm

    try:
        raw_output = call_reviewer_llm(prompt, model=model)
    except ImportError:
        click.echo("LLM support not installed. Run: pip install engram[llm]", err=True)
        return
    except LLMError as e:
        click.echo(f"Review skipped: {e}", err=True)
        return

    try:
        output = reviewer_obj.parse_review_output(raw_output)
    except ValueError as e:
        click.echo(f"Failed to parse LLM output: {e}", err=True)
        return

    report = reviewer_obj.execute_decisions(output, session_id=sid)

    # Clean up session injection file
    cleanup_session_file(store.root, sid)

    # Auto-promote engrams that meet lifecycle thresholds
    lm = LifecycleManager(store)
    proposals = lm.check_transitions()
    promoted: list[str] = []
    for prop in proposals:
        try:
            lm.apply_transition(prop.slug, prop.target_state, prop.reason)
            promoted.append(f"{prop.slug} → {prop.target_state.value}")
        except ValueError:
            pass

    # Write review result for next-session notification (include promotions)
    _write_review_result(store.root, sid, report, promoted)

    if report.created:
        click.echo(f"Created: {', '.join(report.created)}")
    if report.updated:
        click.echo(f"Updated: {', '.join(report.updated)}")
    if report.evaluated:
        click.echo(f"Evaluated: {', '.join(report.evaluated)}")
    if report.blocked:
        click.echo(f"Blocked by scanner: {', '.join(report.blocked)}")
    for err in report.errors:
        click.echo(f"Error: {err}", err=True)
    if report.skipped:
        click.echo(f"Skipped: {report.skipped}")
    if promoted:
        click.echo(f"Promoted: {', '.join(promoted)}")
    if not (report.created or report.updated or report.evaluated or promoted):
        click.echo("No engrams created, updated, or evaluated.")


def _write_review_result(
    store_root: Path, session_id: str, report: object,
    promoted: list[str] | None = None,
) -> None:
    """Write a review result JSON for next-session notification."""
    import json

    reviews_dir = store_root / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "session_id": session_id,
        "ts": datetime.now(tz=UTC).isoformat(),
        "created": getattr(report, "created", []),
        "updated": getattr(report, "updated", []),
        "evaluated": getattr(report, "evaluated", []),
        "promoted": promoted or [],
        "skipped": getattr(report, "skipped", 0),
        "errors": getattr(report, "errors", []),
    }
    result_path = reviews_dir / f"{session_id}.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def _check_pending_reviews(store_root: Path) -> str:
    """Check for completed background reviews and return a summary.

    Consumes (deletes) the review result files after reading them.
    """
    import json

    reviews_dir = store_root / "reviews"
    if not reviews_dir.exists():
        return ""

    summaries: list[str] = []
    for result_file in reviews_dir.glob("*.json"):
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            parts: list[str] = []
            if data.get("created"):
                parts.append(f"created: {', '.join(data['created'])}")
            if data.get("updated"):
                parts.append(f"updated: {', '.join(data['updated'])}")
            if data.get("evaluated"):
                parts.append(f"evaluated: {', '.join(data['evaluated'])}")
            if data.get("promoted"):
                parts.append(f"promoted: {', '.join(data['promoted'])}")
            if data.get("errors"):
                parts.append(f"errors: {len(data['errors'])}")
            if parts:
                summaries.append(f"[engram review] {'; '.join(parts)}")
            result_file.unlink()
        except (json.JSONDecodeError, OSError):
            continue

    # Also clean up any .log files from background processes
    for log_file in reviews_dir.glob("*.log"):
        try:
            log_file.unlink()
        except OSError:
            pass

    if not summaries:
        return ""
    return "\n".join(summaries)


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
