"""Engram Reviewer — builds prompts and parses LLM output for engram creation/update."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from engram.fuzzy_patch import Patch, PatchType, apply_patch
from engram.models import Engram, ReviewDecision, ReviewOutput, ReviewReport
from engram.scanner import EngramScanner
from engram.store import EngramStore

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Regex to extract JSON from markdown code fences
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


class EngramReviewer:
    """Analyzes session transcripts and creates/updates engrams.

    Does NOT call an LLM directly. Provides methods to build prompts
    and parse structured output. The actual LLM call is done externally.
    """

    def __init__(
        self,
        store: EngramStore,
        scanner: EngramScanner | None = None,
    ) -> None:
        self._store = store
        self._scanner = scanner
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
            keep_trailing_newline=True,
        )

    def build_review_prompt(self, session_context: Mapping[str, object]) -> str:
        """Build the review prompt from session context.

        session_context should have:
        - project_path: str
        - session_id: str
        - tool_calls: list[dict] (filtered transcript)
        - outcome: str (success|failure|partial|unknown)

        Returns a prompt string that includes:
        - System instruction for the Reviewer role
        - Session metadata
        - Existing engram index (names + descriptions for dedup)
        - Filtered transcript
        - Output format instructions (JSON schema)
        """
        project_path = session_context.get("project_path", "")
        session_id = session_context.get("session_id", "")
        tool_calls = session_context.get("tool_calls", [])
        outcome = session_context.get("outcome", "unknown")

        # Build engram index for dedup context (limited for LLM context budget)
        index = self._store.read_index()
        engram_index_lines: list[str] = []
        for slug, entry in sorted(index.engrams.items()):
            engram_index_lines.append(f"- {slug}: {entry.description}")
        if len(engram_index_lines) > 50:
            total = len(engram_index_lines)
            engram_index_lines = engram_index_lines[-50:]
            engram_index_lines.insert(0, f"(showing 50 of {total} engrams)")
        engram_index = "\n".join(engram_index_lines) if engram_index_lines else "(none)"

        # Format tool calls as JSON for the prompt
        tool_calls_text = json.dumps(tool_calls, indent=2) if tool_calls else "(none)"

        # Build injected engrams section if any were active this session
        raw_injected = session_context.get("injected_slugs", [])
        injected_slugs: list[str] = (
            list(raw_injected) if isinstance(raw_injected, (list, tuple)) else []
        )
        injected_section = ""
        if injected_slugs:
            lines = [
                "\n## Injected Engrams (active this session)",
                "These engrams were injected into the session context. "
                "Evaluate whether the assistant followed each one:",
            ]
            for slug in injected_slugs:
                maybe_entry = index.engrams.get(slug)
                desc = maybe_entry.description if maybe_entry else "(unknown)"
                lines.append(f"- {slug}: {desc}")
            lines.append(
                '\nFor each injected engram, add an "evaluate" decision '
                "with:\n"
                '- "action": "evaluate"\n'
                '- "target": "<slug>"\n'
                '- "outcome": "success" (was injected and helped) | "override" '
                '(was injected but ignored) | "unused" (was injected but not relevant)\n'
                '- "reason": "brief explanation"'
            )
            injected_section = "\n".join(lines)

        prompt = f"""You are the Engram Reviewer. Your job is to analyze a Claude Code session \
transcript and decide what procedural knowledge to capture as engrams.

## Session Metadata
- Project: {project_path}
- Session ID: {session_id}
- Outcome: {outcome}

## Existing Engrams (for deduplication)
{engram_index}
{injected_section}

## Session Transcript (tool calls)
{tool_calls_text}

## Instructions
Review the session above. For each piece of reusable procedural knowledge:
1. Check if an existing engram already covers it (if so, decide "update" or "skip")
2. If it's new knowledge, decide "create"
3. If it's not worth capturing, decide "skip"
4. For non-injected existing engrams whose knowledge would have been useful in this session, \
add an "evaluate" decision with "outcome": "relevant" — this is weaker than "success" \
(which is reserved for engrams that were actually injected and helped)

## Output Format
Respond with a JSON object matching this schema:
```json
{{
  "decisions": [
    {{
      "action": "create" | "update" | "skip" | "evaluate",
      "engram": {{...}}  // For create: full Engram object
      "target": "slug",  // For update/evaluate: slug of engram
      "patch": {{...}},  // For update: patch data
      "outcome": "success" | "override" | "unused" | "relevant",  // For evaluate only
      "reason": "why"
    }}
  ]
}}
```

For "create" decisions, the engram object must include: name (slug), version (1), \
description, state ("draft"), created, updated, trust ("agent-created"), triggers, and body.

For "update" decisions, provide patch_type ("append", "replace_section", \
"frontmatter_merge", or "full_rewrite") and corresponding content in the patch field.
"""
        return prompt

    def parse_review_output(self, raw_output: str) -> ReviewOutput:
        """Parse the structured JSON output from the Reviewer LLM call.

        Extracts JSON from the raw output (handles markdown code fences).
        Validates against ReviewOutput schema.
        Returns ReviewOutput with parsed decisions.
        Raises ValueError if JSON is malformed or doesn't validate.
        """
        json_str = self._extract_json(raw_output)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in reviewer output: {e}") from e

        try:
            return ReviewOutput.model_validate(data)
        except Exception as e:
            raise ValueError(f"Reviewer output does not match schema: {e}") from e

    def execute_decisions(
        self,
        output: ReviewOutput,
        session_id: str = "",
    ) -> ReviewReport:
        """Execute reviewer decisions: create, update, evaluate, or skip.

        For each decision:
        - "create": Scan the engram, write to store if allowed
        - "update": Load existing, apply fuzzy patch, scan result, write if allowed
        - "evaluate": Record success/override signal and update score
        - "skip": Count it

        Blocked engrams (scanner rejects) are logged in the report.
        All new engrams from create decisions get trust=agent-created, state=draft.
        """
        report = ReviewReport()

        for decision in output.decisions:
            if decision.action == "skip":
                report.skipped += 1
            elif decision.action == "create":
                self._execute_create(decision, report)
            elif decision.action == "update":
                self._execute_update(decision, report)
            elif decision.action == "evaluate":
                self._execute_evaluate(decision, report, session_id)

        return report

    def review_session(self, session_context: Mapping[str, object]) -> ReviewReport:
        """High-level: validate session_context, return empty ReviewReport.

        This method is for the non-LLM path: it validates session_context
        and returns an empty ReviewReport. The actual LLM interaction happens
        externally (Claude Code agent or anthropic SDK).

        For testing: use build_review_prompt + parse_review_output + execute_decisions.
        """
        # Validate that required keys are present
        _required = ("project_path", "session_id", "tool_calls", "outcome")
        for key in _required:
            if key not in session_context:
                raise ValueError(f"Missing required key in session_context: {key}")

        return ReviewReport()

    def render_engram_template(self, **kwargs: object) -> str:
        """Render the Jinja2 engram template with the given variables."""
        template = self._jinja_env.get_template("engram.md.j2")
        return template.render(**kwargs)

    def load_transcript(self, session_path: Path) -> list[dict[str, object]]:
        """Load a Claude Code session JSONL transcript.

        Each line is a JSON record. Malformed lines are silently skipped.
        Raises FileNotFoundError if the file doesn't exist.
        """
        if not session_path.exists():
            raise FileNotFoundError(f"Session file not found: {session_path}")
        records: list[dict[str, object]] = []
        with open(session_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def filter_transcript(
        self,
        records: list[dict[str, object]],
        last_n: int | None = None,
    ) -> list[dict[str, object]]:
        """Filter a raw transcript to relevant records.

        Removes:
        - file-history-snapshot records (internal bookkeeping)
        - sidechain records (parallel branches, not main conversation)

        Keeps user messages, assistant messages, tool use, tool results.
        Optionally limits to the last N records.
        """
        filtered = [
            r for r in records
            if r.get("type") in ("user", "assistant")
            and not r.get("isSidechain", False)
        ]
        if last_n is not None:
            filtered = filtered[-last_n:]
        return filtered

    # Maximum tool calls to include in the review prompt.
    # Keeps LLM prompt size reasonable for long sessions.
    MAX_TOOL_CALLS = 50

    # Maximum characters per tool input to include.
    # Large file reads / outputs are truncated.
    MAX_INPUT_CHARS = 500

    def build_context_from_transcript(
        self,
        session_path: Path,
        project_path: str = "",
        session_id: str = "",
    ) -> dict[str, object]:
        """Build a session_context dict from a transcript file.

        Loads the transcript, filters it, and packages it into the format
        expected by build_review_prompt().
        """
        raw = self.load_transcript(session_path)
        filtered = self.filter_transcript(raw)

        # Extract tool calls for the prompt
        tool_calls: list[dict[str, object]] = []
        for record in filtered:
            msg = record.get("message", {})
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        raw_input = block.get("input", {})
                        # Truncate large tool inputs
                        input_str = str(raw_input)
                        if len(input_str) > self.MAX_INPUT_CHARS:
                            truncated_input = input_str[:self.MAX_INPUT_CHARS] + "...(truncated)"
                        else:
                            truncated_input = raw_input
                        tool_calls.append({
                            "tool": block.get("name", ""),
                            "input": truncated_input,
                        })

        # Keep only the last N tool calls for the LLM prompt
        if len(tool_calls) > self.MAX_TOOL_CALLS:
            tool_calls = tool_calls[-self.MAX_TOOL_CALLS:]

        return {
            "project_path": project_path,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "outcome": "unknown",
        }

    def render_skill_template(self, engram: Engram) -> str:
        """Render an engram as a Claude Code SKILL.md file.

        Transforms the engram into SKILL.md format with frontmatter
        containing name, description, and allowed-tools.
        """
        template = self._jinja_env.get_template("skill.md.j2")
        return template.render(
            name=engram.name,
            description=engram.description,
            allowed_tools=engram.allowed_tools,
            body=engram.body,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_json(self, raw: str) -> str:
        """Extract JSON from raw output, handling markdown code fences."""
        # Try to find JSON in code fences first
        match = _JSON_FENCE_RE.search(raw)
        if match:
            return match.group(1).strip()

        # Try the raw string as-is (it might just be JSON)
        stripped = raw.strip()
        if stripped.startswith("{"):
            return stripped

        raise ValueError("No JSON found in reviewer output")

    def _execute_create(self, decision: ReviewDecision, report: ReviewReport) -> None:
        """Execute a create decision."""
        if decision.engram is None:
            report.errors.append("Create decision missing engram data")
            return

        engram = decision.engram

        # Dedup check: reject high-confidence duplicates
        from engram.lifecycle import LifecycleManager

        lm = LifecycleManager(self._store)
        dupes = lm.check_duplicates(engram)
        high_confidence = [d for d in dupes if d.similarity_score >= 0.7]
        if high_confidence:
            best = max(high_confidence, key=lambda d: d.similarity_score)
            report.errors.append(
                f"Skipped duplicate '{engram.name}' "
                f"(similar to '{best.slug}', score={best.similarity_score:.2f})"
            )
            return

        # Scan before writing
        if self._scanner is not None:
            verdict = self._scanner.scan(engram)
            if verdict.action == "block":
                report.blocked.append(engram.name)
                return

        self._store.write(engram)
        report.created.append(engram.name)

    def _execute_update(self, decision: ReviewDecision, report: ReviewReport) -> None:
        """Execute an update decision."""
        if decision.target is None:
            report.errors.append("Update decision missing target slug")
            return
        if decision.patch is None:
            report.errors.append(f"Update decision for {decision.target} missing patch data")
            return

        try:
            engram = self._store.read(decision.target)
        except FileNotFoundError:
            report.errors.append(f"Engram not found for update: {decision.target}")
            return

        # Build a Patch from the decision's patch data
        patch_data = decision.patch
        try:
            patch_type = PatchType(patch_data.get("patch_type", "append"))
            patch = Patch(
                patch_type=patch_type,
                content=patch_data.get("content", ""),
                section_heading=patch_data.get("section_heading", ""),
                frontmatter_updates=patch_data.get("frontmatter_updates", {}),
            )
        except (ValueError, KeyError) as e:
            report.errors.append(f"Invalid patch for {decision.target}: {e}")
            return

        updated = apply_patch(engram, patch)

        # Scan the updated engram before writing
        if self._scanner is not None:
            verdict = self._scanner.scan(updated)
            if verdict.action == "block":
                report.blocked.append(decision.target)
                return

        self._store.write(updated)
        report.updated.append(decision.target)

    def _execute_evaluate(
        self,
        decision: ReviewDecision,
        report: ReviewReport,
        session_id: str,
    ) -> None:
        """Execute an evaluate decision — record success/override signal."""
        if decision.target is None:
            report.errors.append("Evaluate decision missing target slug")
            return
        if decision.outcome is None or decision.outcome == "unused":
            return

        from engram.evaluator import EngramEvaluator
        from engram.hooks import record_signal

        try:
            self._store.read(decision.target)
        except FileNotFoundError:
            report.errors.append(
                f"Engram not found for evaluate: {decision.target}"
            )
            return

        record_signal(
            self._store.root,
            decision.target,
            decision.outcome,
            session_id,
        )
        evaluator = EngramEvaluator(self._store)
        evaluator.update_engram_score(decision.target)
        report.evaluated.append(decision.target)
