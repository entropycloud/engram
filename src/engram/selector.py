"""Engram selector — context-aware retrieval with ranked scoring."""

from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime

from engram.models import (
    EngramState,
    ScoredEngram,
    SessionContext,
)
from engram.store import EngramStore

# States eligible for selection
_ELIGIBLE_STATES = frozenset({EngramState.CANDIDATE, EngramState.STABLE})

# Tag match threshold — engrams with tag_score below this are filtered out
_TAG_THRESHOLD = 0.3

# Scoring weights
_W_TAG = 0.4
_W_QUALITY = 0.3
_W_STATE = 0.2
_W_RECENCY = 0.1

# Token estimation: ~4 chars per token
_CHARS_PER_TOKEN = 4

# Tier token budgets
_HIGH_BUDGET_TOKENS = 2000
_MEDIUM_BUDGET_TOKENS = 1000
_LOW_BUDGET_TOKENS = 500

# Confidence thresholds
_HIGH_THRESHOLD = 0.8
_MEDIUM_THRESHOLD = 0.5


class EngramSelector:
    """Select and rank engrams relevant to a session context."""

    def __init__(self, store: EngramStore, token_budget: int = 3500) -> None:
        self.store = store
        self.token_budget = token_budget

    def select(self, context: SessionContext) -> list[ScoredEngram]:
        """Return ranked engrams matching the current context, within token budget.

        6-stage pipeline, cheapest filters first.
        """
        index = self.store.read_index()

        scored: list[ScoredEngram] = []
        for slug, entry in index.engrams.items():
            # Stage 1: State filter
            if entry.state not in _ELIGIBLE_STATES:
                continue

            # Stage 2: Project filter
            if entry.projects and context.project_path is not None:
                if not _matches_any_glob(context.project_path, entry.projects):
                    continue
            elif entry.projects and context.project_path is None:
                # Engram requires specific projects but no project in context
                continue

            # Stage 3: File filter
            if entry.files and not _matches_any_file_glob(context.files, entry.files):
                continue

            # Stage 4: Tag match (context tags OR prompt-derived tags)
            tag_score = _compute_tag_score(entry.tags, context.tags)
            prompt_tag_score = _compute_prompt_tag_score(entry.tags, context.prompt)
            effective_tag_score = max(tag_score, prompt_tag_score)
            if entry.tags and effective_tag_score < _TAG_THRESHOLD and not entry.patterns:
                continue

            # Stage 5: Pattern match
            pattern_matched = _check_patterns(entry.patterns, context.prompt)

            # Load full engram for scoring and result
            engram = self.store.read(slug)

            # Stage 6: Ranking
            state_bonus = 1.0 if entry.state == EngramState.STABLE else 0.5
            recency_bonus = _compute_recency_bonus(engram.metrics.last_used)
            score = (
                effective_tag_score * _W_TAG
                + entry.quality_score * _W_QUALITY
                + state_bonus * _W_STATE
                + recency_bonus * _W_RECENCY
            )

            match_reasons: list[str] = []
            if tag_score > 0:
                match_reasons.append("tag")
            if prompt_tag_score > 0:
                match_reasons.append("prompt_tag")
            if pattern_matched:
                match_reasons.append("pattern")

            scored.append(ScoredEngram(
                slug=slug,
                engram=engram,
                score=round(score, 4),
                match_reasons=match_reasons,
            ))

        # Sort descending by score
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def format_injection(self, scored_engrams: list[ScoredEngram]) -> str:
        """Format selected engrams for injection into session context.

        Token budget tiers:
        - High confidence (score >= 0.8): Up to 2000 tokens total - full body
        - Medium confidence (0.5-0.8): Up to 1000 tokens total - full body
        - Low confidence (0.3-0.5): Up to 500 tokens total - summary only
        """
        if not scored_engrams:
            return ""

        lines: list[str] = [
            "## Active Engrams",
            "",
            "The following procedural knowledge is relevant to this session:",
            "",
        ]

        high_budget = _HIGH_BUDGET_TOKENS * _CHARS_PER_TOKEN
        medium_budget = _MEDIUM_BUDGET_TOKENS * _CHARS_PER_TOKEN
        low_budget = _LOW_BUDGET_TOKENS * _CHARS_PER_TOKEN
        total_budget = self.token_budget * _CHARS_PER_TOKEN

        high_used = 0
        medium_used = 0
        low_used = 0
        total_used = len("\n".join(lines))

        for se in scored_engrams:
            engram = se.engram
            header = (
                f"### [{engram.state.value}] {engram.description} "
                f"(v{engram.version}, score: {se.score})"
            )

            if se.score >= _HIGH_THRESHOLD:
                body_text = engram.body
                entry_text = f"{header}\n{body_text}\n"
                entry_chars = len(entry_text)
                if high_used + entry_chars > high_budget:
                    continue
                if total_used + entry_chars > total_budget:
                    continue
                high_used += entry_chars
                total_used += entry_chars
                lines.append(entry_text)

            elif se.score >= _MEDIUM_THRESHOLD:
                body_text = engram.body
                entry_text = f"{header}\n{body_text}\n"
                entry_chars = len(entry_text)
                if medium_used + entry_chars > medium_budget:
                    continue
                if total_used + entry_chars > total_budget:
                    continue
                medium_used += entry_chars
                total_used += entry_chars
                lines.append(entry_text)

            else:
                summary = (
                    f"{header}\n"
                    f"[Summary only — full engram available via "
                    f"`engram view {se.slug}`]\n"
                    f"{engram.description}\n"
                )
                entry_chars = len(summary)
                if low_used + entry_chars > low_budget:
                    continue
                if total_used + entry_chars > total_budget:
                    continue
                low_used += entry_chars
                total_used += entry_chars
                lines.append(summary)

        return "\n".join(lines)


def _matches_any_glob(value: str, patterns: list[str]) -> bool:
    """Check if value matches any of the glob patterns."""
    return any(fnmatch.fnmatch(value, pat) for pat in patterns)


def _matches_any_file_glob(context_files: list[str], file_patterns: list[str]) -> bool:
    """Check if any context file matches any of the file glob patterns."""
    for ctx_file in context_files:
        for pat in file_patterns:
            if fnmatch.fnmatch(ctx_file, pat):
                return True
    return False


def _compute_tag_score(engram_tags: list[str], context_tags: list[str]) -> float:
    """Compute tag overlap score: |intersection| / |engram_tags|."""
    if not engram_tags:
        return 0.0
    if not context_tags:
        return 0.0
    intersection = set(engram_tags) & set(context_tags)
    return len(intersection) / len(engram_tags)


def _compute_prompt_tag_score(engram_tags: list[str], prompt: str) -> float:
    """Score tag relevance by matching tag words against prompt words."""
    if not engram_tags or not prompt:
        return 0.0
    prompt_words = set(prompt.lower().split())
    tag_words: set[str] = set()
    for tag in engram_tags:
        for word in tag.lower().split():
            tag_words.add(word)
    if not tag_words:
        return 0.0
    matched = sum(1 for tw in tag_words if tw in prompt_words)
    return matched / len(tag_words)


def _check_patterns(patterns: list[str], prompt: str) -> bool:
    """Check if any regex pattern matches the prompt. Skips invalid patterns."""
    if not prompt or not patterns:
        return False
    for pat in patterns:
        try:
            compiled = re.compile(pat)
            if compiled.search(prompt):
                return True
        except re.error:
            continue
    return False


def _compute_recency_bonus(last_used: datetime | None) -> float:
    """Compute recency bonus: 1.0 if last 7 days, 0.5 if last 30, 0.0 otherwise."""
    if last_used is None:
        return 0.0
    now = datetime.now(tz=UTC)
    days_since = (now - last_used).days
    if days_since <= 7:
        return 1.0
    if days_since <= 30:
        return 0.5
    return 0.0
