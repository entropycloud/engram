"""Engram data models — Pydantic v2."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TrustLevel(StrEnum):
    """Trust level determines scanning policy and tool allowlists."""

    SYSTEM = "system"
    VERIFIED = "verified"
    COMMUNITY = "community"
    AGENT_CREATED = "agent-created"


class EngramState(StrEnum):
    """Lifecycle state of an engram."""

    DRAFT = "draft"
    CANDIDATE = "candidate"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class Triggers(BaseModel):
    """Activation triggers for engram matching."""

    tags: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


class Metrics(BaseModel):
    """Quality metrics populated by the Evaluator."""

    usage_count: int = 0
    success_count: int = 0
    override_count: int = 0
    relevant_count: int = 0
    last_used: datetime | None = None
    last_evaluated: datetime | None = None
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    streak: int = 0


class Lineage(BaseModel):
    """Creation lineage for tracking engram provenance."""

    parent: str | None = None
    created_from: str | None = None
    creation_reason: str | None = None


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Engram(BaseModel):
    """Core engram data model — a unit of procedural memory."""

    # Identity
    name: str
    version: int = Field(ge=1)
    description: str

    # Lifecycle
    state: EngramState = EngramState.DRAFT
    created: datetime
    updated: datetime
    pinned: bool = False
    supersedes: str | None = None
    superseded_by: str | None = None

    # Activation
    triggers: Triggers = Field(default_factory=Triggers)

    @field_validator("triggers", mode="before")
    @classmethod
    def _coerce_triggers(cls, v: Any) -> Any:
        """Accept a flat list of strings as tags-only triggers."""
        if isinstance(v, list):
            return {"tags": v}
        return v

    # Trust & Security
    trust: TrustLevel = TrustLevel.AGENT_CREATED
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read"])
    restricted_tools: list[str] = Field(default_factory=list)

    # Quality (populated by Evaluator)
    metrics: Metrics = Field(default_factory=Metrics)

    # Lineage
    lineage: Lineage = Field(default_factory=Lineage)

    # Body (markdown content below the frontmatter)
    body: str = ""

    @field_validator("name")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                f"Name must be a URL-safe slug (lowercase alphanumeric, hyphens, "
                f"underscores, must start with alphanumeric): {v!r}"
            )
        return v


class MetricEvent(BaseModel):
    """A single metric event recorded in a JSONL sidecar."""

    ts: datetime
    event: Literal["used", "success", "override", "feedback", "session_end", "tool_use"]
    session: str
    context: str | None = None
    detail: str | None = None
    rating: Literal["up", "down"] | None = None


class IndexEntry(BaseModel):
    """Denormalized index entry for fast lookups."""

    description: str
    state: EngramState
    trust: TrustLevel
    quality_score: float
    tags: list[str]
    patterns: list[str]
    projects: list[str]
    files: list[str]
    updated: datetime
    version: int
    pinned: bool = False

    @classmethod
    def from_engram(cls, engram: Engram) -> IndexEntry:
        return cls(
            description=engram.description,
            state=engram.state,
            trust=engram.trust,
            quality_score=engram.metrics.quality_score,
            tags=engram.triggers.tags,
            patterns=engram.triggers.patterns,
            projects=engram.triggers.projects,
            files=engram.triggers.files,
            updated=engram.updated,
            version=engram.version,
            pinned=engram.pinned,
        )


class StoreIndex(BaseModel):
    """Fast-lookup index rebuilt from engram files."""

    version: int = 1
    rebuilt_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    engrams: dict[str, IndexEntry] = Field(default_factory=dict)


class ScanResult(BaseModel):
    """A single finding from the security scanner."""

    severity: Literal["info", "warning", "critical"]
    category: str
    pattern_id: str
    matched_text: str
    line_number: int
    message: str


class ScanVerdict(BaseModel):
    """Overall verdict from scanning an engram."""

    action: Literal["allow", "warn", "block"]
    results: list[ScanResult] = Field(default_factory=list)


class TransitionProposal(BaseModel):
    """Proposed state transition for an engram."""

    slug: str
    current_state: EngramState
    target_state: EngramState
    reason: str


class DedupCandidate(BaseModel):
    """A potential duplicate engram."""

    slug: str
    similarity_type: Literal["tag_overlap", "description_similarity"]
    similarity_score: float
    description: str


class GCReport(BaseModel):
    """Report from garbage collection run."""

    archived: list[str] = Field(default_factory=list)
    orphan_metrics_cleaned: list[str] = Field(default_factory=list)
    orphan_versions_cleaned: list[str] = Field(default_factory=list)


class SessionContext(BaseModel):
    """Context for the current session, used by the Selector for matching."""

    project_path: str | None = None
    files: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    prompt: str = ""


class ScoredEngram(BaseModel):
    """An engram with a computed relevance score."""

    slug: str
    engram: Engram
    score: float
    match_reasons: list[str] = Field(default_factory=list)


class ReviewDecision(BaseModel):
    """A single decision from the Reviewer."""

    action: Literal["create", "update", "skip", "evaluate"]
    # For create:
    engram: Engram | None = None
    # For update:
    target: str | None = None  # slug of engram to update
    patch: dict[str, Any] | None = None  # patch data
    # For evaluate:
    outcome: Literal["success", "override", "unused", "relevant"] | None = None
    # Common:
    reason: str = ""


class ReviewOutput(BaseModel):
    """Structured output from the Reviewer agent."""

    decisions: list[ReviewDecision] = Field(default_factory=list)


class ReviewReport(BaseModel):
    """Report of what the Reviewer did."""

    created: list[str] = Field(default_factory=list)  # slugs created
    updated: list[str] = Field(default_factory=list)  # slugs updated
    evaluated: list[str] = Field(default_factory=list)  # slugs evaluated
    skipped: int = 0
    blocked: list[str] = Field(default_factory=list)  # slugs blocked by scanner
    errors: list[str] = Field(default_factory=list)
