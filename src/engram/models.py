"""Engram data models — Pydantic v2."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

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
    supersedes: str | None = None
    superseded_by: str | None = None

    # Activation
    triggers: Triggers = Field(default_factory=Triggers)

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
    event: Literal["used", "success", "override", "feedback"]
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
