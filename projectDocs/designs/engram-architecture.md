---
Status: Draft
Created: 2026-04-01
Updated: 2026-04-01
Related: ../research/2026-04-01-hermes-agent-skill-system-analysis.md
Tags: architecture, engram, procedural-memory, claude-code
---

# Engram: Self-Improving Procedural Memory for Claude Code

## 1. System Overview

Engram is a procedural memory system that lets Claude Code learn from experience. Where Claude Code's existing memory (`MEMORY.md`) stores declarative facts ("this project uses pytest"), engrams store procedural knowledge ("when deploying this project, always run migrations before restarting the service because the startup healthcheck queries new tables").

Engrams are created automatically when the agent discovers non-trivial approaches, evaluated based on real outcomes, and improved or retired based on quality signals over time.

### System Context Diagram

```
+------------------------------------------------------------------+
|                        Claude Code Session                       |
|                                                                  |
|  +------------------+    +------------------+   +--------------+ |
|  |   CLAUDE.md      |    |   MEMORY.md      |   | SKILL.md     | |
|  | (instructions)   |    | (declarative     |   | (user-       | |
|  |                  |    |  facts)          |   |  defined)    | |
|  +------------------+    +------------------+   +--------------+ |
|           |                       |                    |         |
|           +----------+------------+--------------------+         |
|                      |                                           |
|                      v                                           |
|  +------------------------------------------------------------+  |
|  |                    Context Assembly                        |  |
|  |  (what the agent sees at the start of each turn)           |  |
|  +------------------------------------------------------------+  |
|           ^                       ^                    ^         |
|           |                       |                    |         |
|  +--------+--------+    +--------+--------+   +-------+-------+  |
|  | Engram Selector  |    | Engram Reviewer |   | Signal       |  |
|  | (reads engrams,  |    | (creates/updates|   | Collector    |  |
|  |  injects relevant|    |  engrams after  |   | (hooks into  |  |
|  |  ones into ctx)  |    |  sessions)      |   |  tool use)   |  |
|  +---------+--------+    +--------+--------+   +-------+-------+ |
|            |                      |                    |         |
+------------------------------------------------------------------+
             |                      |                    |
             v                      v                    v
  +------------------------------------------------------------+
  |                     Engram Store                            |
  |  ~/.claude/engrams/          (global)                      |
  |  .engram/                    (project-level)               |
  |                                                            |
  |  +----------+  +----------+  +-----------+  +----------+  |
  |  | engram/  |  | index.   |  | metrics/  |  | archive/ |  |
  |  | *.md     |  | json     |  | *.jsonl   |  | *.md     |  |
  |  +----------+  +----------+  +-----------+  +----------+  |
  +------------------------------------------------------------+
             |                      |
             v                      v
  +------------------------+  +-------------------------+
  | Engram Scanner         |  | Lifecycle Manager       |
  | (security validation)  |  | (state transitions,     |
  |                        |  |  dedup, versioning)     |
  +------------------------+  +-------------------------+
```

### Data Flow

```
Session Start:
  Selector reads index -> matches engrams to project context -> injects into prompt

During Session:
  Signal Collector hooks PostToolUse -> records usage, outcomes -> writes metrics/*.jsonl

Session End / Stop Hook:
  Reviewer agent forks -> analyzes session transcript ->
    creates new engrams OR patches existing ones -> Scanner validates -> Store writes

Background (periodic / on-demand):
  Lifecycle Manager reads metrics -> computes quality scores ->
    promotes/demotes/archives engrams -> deduplicates -> updates index
```


## 2. Core Concept: What Is an Engram?

An engram is a unit of procedural memory. It captures *how to do something* that the agent learned through experience, not through explicit instruction.

### Properties of an Engram

| Property | Description |
|----------|-------------|
| **Created** | When the agent discovers a non-trivial approach by trial and error, user correction, or multi-step reasoning |
| **Used** | When the Selector matches it to the current task and injects it into context |
| **Evaluated** | By tracking whether tasks succeeded after the engram was applied |
| **Improved** | When the Reviewer detects better approaches or the Evaluator flags declining quality |
| **Retired** | When quality drops below threshold or a superseding engram exists |

### What Makes a Good Engram

An engram should be:

1. **Procedural, not declarative.** "Run `alembic upgrade head` before `systemctl restart app` because the healthcheck queries the users_v2 table" -- not "this project uses Alembic."
2. **Contextual.** It specifies *when* it applies, not just *what* to do.
3. **Falsifiable.** You can tell whether following it helped or hurt.
4. **Atomic.** One procedure per engram. Composable, not monolithic.
5. **Versionable.** It can evolve as the codebase changes.

### What an Engram Is NOT

- Not a skill. Skills are user-defined capabilities with tool permissions. Engrams are agent-learned procedures.
- Not a memory. Memories are declarative project facts. Engrams are procedural knowledge.
- Not a hook rule. Hook rules are reactive guards. Engrams are proactive guidance.

### Relationship to Existing Claude Code Primitives

```
CLAUDE.md        = constitution    (always loaded, global rules)
MEMORY.md        = declarative     (facts about the project)
SKILL.md         = capabilities    (user-defined tools/workflows)
engram/*.md      = procedural      (agent-learned how-to knowledge)
hookify rules    = reactive guards (prevent bad actions)
```

An engram *can* graduate into a skill if the user promotes it, but by default engrams live in their own namespace and have their own lifecycle.


## 3. Engram Format (Data Model)

An engram file is a Markdown file with YAML frontmatter, stored at a well-known path. The format deliberately extends Claude Code's `SKILL.md` frontmatter so that promotion to a skill is a metadata change, not a rewrite.

### File: `~/.claude/engrams/engram/<slug>.md`

```yaml
---
# === Identity ===
name: "run-migrations-before-restart"
version: 3
description: "Run Alembic migrations before restarting the app service"

# === Lifecycle ===
state: candidate          # draft | candidate | stable | deprecated | archived
created: "2026-03-15T10:30:00Z"
updated: "2026-03-28T14:20:00Z"
supersedes: ~             # slug of engram this replaces, if any
superseded_by: ~          # slug of engram that replaced this, if any

# === Activation ===
triggers:
  tags: [deploy, alembic, migration, systemctl]
  patterns:               # regex patterns matched against user prompt + file context
    - "deploy|restart.*service|systemctl.*restart"
    - "alembic|migration"
  projects:               # project path globs where this engram is relevant
    - "/data/dev/myapp/*"
  files:                  # file path globs that signal relevance
    - "**/alembic.ini"
    - "**/alembic/versions/*.py"

# === Trust & Security ===
trust: agent-created      # system | verified | community | agent-created
allowed_tools:            # tools the engram may reference (sandboxing)
  - Bash
  - Read
  - Edit
restricted_tools: []      # tools explicitly denied

# === Quality Metrics (populated by Evaluator) ===
metrics:
  usage_count: 12
  success_count: 10
  override_count: 1       # times user explicitly ignored/overrode
  last_used: "2026-03-28T14:20:00Z"
  last_evaluated: "2026-03-28T14:20:00Z"
  quality_score: 0.82     # computed: see Evaluator section
  streak: 3               # consecutive successes since last failure

# === Lineage ===
lineage:
  parent: ~               # slug of parent engram if this was refined from another
  created_from: "session-2026-03-15-abc123"   # session ID that spawned this
  creation_reason: "agent discovered migration ordering dependency after deployment failure"
---

# Run Migrations Before Restart

## When to Apply

When deploying changes to the myapp service that include Alembic migration files.

## Procedure

1. Check for pending migrations: `alembic heads` vs `alembic current`
2. If pending, run `alembic upgrade head` on the target environment
3. Verify migration success by checking alembic_version table
4. Only then restart the service: `systemctl restart myapp`

## Why This Matters

The application healthcheck endpoint queries the `users_v2` table, which was
introduced in migration `abc123_add_users_v2`. If the service restarts before
the migration runs, the healthcheck fails, the load balancer marks the instance
unhealthy, and the deployment rolls back -- silently losing the migration.

## Failure Mode

If this procedure is NOT followed: deployment appears to succeed but the
healthcheck fails within 30s, causing a rollback cascade.
```

### Full Frontmatter Schema

| Field | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `name` | `string` | -- | yes | URL-safe slug, unique within the store |
| `version` | `integer` | `1` | yes | Monotonically increasing version number |
| `description` | `string` | -- | yes | One-line human-readable summary |
| `state` | `enum` | `"draft"` | yes | Lifecycle state: `draft`, `candidate`, `stable`, `deprecated`, `archived` |
| `created` | `datetime` | -- | yes | ISO 8601 creation timestamp |
| `updated` | `datetime` | -- | yes | ISO 8601 last-modified timestamp |
| `supersedes` | `string?` | `null` | no | Slug of the engram this one replaced |
| `superseded_by` | `string?` | `null` | no | Slug of the engram that replaced this one |
| `triggers.tags` | `string[]` | `[]` | no | Keyword tags for matching |
| `triggers.patterns` | `string[]` | `[]` | no | Regex patterns matched against task context |
| `triggers.projects` | `string[]` | `[]` | no | Project path globs for scoping |
| `triggers.files` | `string[]` | `[]` | no | File path globs that signal relevance |
| `trust` | `enum` | `"agent-created"` | yes | Trust level: `system`, `verified`, `community`, `agent-created` |
| `allowed_tools` | `string[]` | `["Read"]` | no | Tools the engram may reference in procedures |
| `restricted_tools` | `string[]` | `[]` | no | Tools explicitly denied |
| `metrics.usage_count` | `integer` | `0` | no | Total times selected for injection |
| `metrics.success_count` | `integer` | `0` | no | Times the task succeeded after injection |
| `metrics.override_count` | `integer` | `0` | no | Times the user explicitly overrode this engram |
| `metrics.last_used` | `datetime?` | `null` | no | Timestamp of most recent use |
| `metrics.last_evaluated` | `datetime?` | `null` | no | Timestamp of most recent quality evaluation |
| `metrics.quality_score` | `float` | `0.0` | no | Computed quality score, range [0.0, 1.0] |
| `metrics.streak` | `integer` | `0` | no | Consecutive successes since last failure |
| `lineage.parent` | `string?` | `null` | no | Slug of parent engram if refined |
| `lineage.created_from` | `string?` | `null` | no | Session ID that spawned this engram |
| `lineage.creation_reason` | `string?` | `null` | no | Why this engram was created |


## 4. Engram Store

### Directory Structure

```
~/.claude/engrams/                    # Global engram store
  engram/                             # Active engram files
    run-migrations-before-restart.md
    pytest-fixture-scoping.md
    ...
  archive/                            # Archived engrams (state=archived)
    deprecated-old-deploy-pattern.md
  metrics/                            # Usage metrics (JSONL sidecars)
    run-migrations-before-restart.jsonl
    pytest-fixture-scoping.jsonl
  versions/                           # Version history
    run-migrations-before-restart/
      v1.md
      v2.md
  index.json                          # Fast-lookup index (rebuilt from engram/*.md)
  store.lock                          # File lock for atomic writes

<project>/.engram/                    # Project-level engram store (same structure)
  engram/
  archive/
  metrics/
  versions/
  index.json
  store.lock
```

### Resolution Order

When the Selector searches for engrams, it merges both stores with project-level taking priority:

1. Read `<project>/.engram/index.json`
2. Read `~/.claude/engrams/index.json`
3. Merge, with project-level engrams winning on slug collision
4. Filter by state (only `candidate` and `stable` are eligible for injection)

### Index File Format

The index is a denormalized lookup structure rebuilt from engram frontmatter. It exists purely for performance -- the engram files are the source of truth.

```json
{
  "version": 1,
  "rebuilt_at": "2026-03-28T14:20:00Z",
  "engrams": {
    "run-migrations-before-restart": {
      "description": "Run Alembic migrations before restarting the app service",
      "state": "candidate",
      "trust": "agent-created",
      "quality_score": 0.82,
      "tags": ["deploy", "alembic", "migration", "systemctl"],
      "patterns": ["deploy|restart.*service|systemctl.*restart", "alembic|migration"],
      "projects": ["/data/dev/myapp/*"],
      "files": ["**/alembic.ini", "**/alembic/versions/*.py"],
      "updated": "2026-03-28T14:20:00Z",
      "version": 3
    }
  }
}
```

### Atomic Write Operations

All writes to the engram store use a write-ahead pattern:

1. Acquire `store.lock` (POSIX advisory lock via `fcntl.flock`, timeout 5s)
2. Write to a temporary file in the same directory: `<slug>.md.tmp`
3. `fsync` the temporary file
4. Atomic rename (`os.rename`) from `<slug>.md.tmp` to `<slug>.md`
5. Rebuild index (write `index.json.tmp`, fsync, rename)
6. Release lock

If the process crashes between steps 2-4, the `.tmp` file is cleaned up on the next store access.

### Caching Strategy

Two-layer caching, adapted from Hermes:

| Layer | Scope | TTL | Invalidation |
|-------|-------|-----|--------------|
| In-process LRU | Parsed engram objects | Session lifetime | On file mtime change |
| Disk index | `index.json` | Until next write | Rebuilt on any engram write |

The in-process cache holds parsed frontmatter for engrams read during the current session. Cache hit is validated by comparing `os.stat().st_mtime_ns` against the cached mtime. This avoids re-parsing YAML on every Selector pass while staying correct if an external process modifies engram files.

The disk index (`index.json`) holds denormalized search fields for all engrams. The Selector reads only the index for matching, then loads the full engram file only for matched entries.


## 5. Components

### 5a. Engram Scanner (Security)

The Scanner validates engram content before it enters the store. It prevents injection attacks, credential leaks, and unsafe tool references.

#### Trust Levels

| Level | Source | Capabilities | Scanning |
|-------|--------|--------------|----------|
| `system` | Shipped with Engram package | All tools, no restrictions | Not scanned (code-reviewed) |
| `verified` | User-created or user-promoted | All tools user has granted | Scanned on create only |
| `community` | Imported from external sources | Restricted tool set | Full scan on import + each use |
| `agent-created` | Created by the Reviewer agent | Restricted tool set, no Bash by default | Full scan on create + each update |

#### Scanning Pipeline

```python
class ScanResult:
    severity: Literal["info", "warning", "critical"]
    category: str          # "credential", "injection", "unsafe_tool", "obfuscation"
    pattern_id: str        # e.g., "CRED-001"
    matched_text: str      # the offending content
    line_number: int
    message: str

class ScanVerdict:
    action: Literal["allow", "warn", "block"]
    results: list[ScanResult]
```

**Pattern categories (150+ patterns adapted from Hermes):**

| Category | Example Patterns | Count |
|----------|-----------------|-------|
| Credentials | API keys, tokens, passwords, AWS keys, private keys | ~40 |
| Shell injection | Backticks, `$(...)`, pipe chains, `eval`, `exec` | ~30 |
| File system | Access to `~/.ssh`, `/etc/shadow`, `~/.aws`, `.env` | ~25 |
| Network | `curl`, `wget`, outbound connections, DNS exfil | ~20 |
| Obfuscation | Base64 blobs, hex encoding, unicode tricks | ~15 |
| Tool abuse | Referencing tools outside `allowed_tools` | ~10 |
| Prompt injection | Instruction override attempts, role hijacking | ~15 |

#### Policy Matrix

The Scanner maps `(trust_level, severity)` to an action:

| | `info` | `warning` | `critical` |
|---|--------|-----------|------------|
| `system` | allow | allow | allow |
| `verified` | allow | allow | warn |
| `community` | allow | warn | block |
| `agent-created` | allow | warn | block |

#### When Scanning Happens

| Event | Scan Scope |
|-------|-----------|
| Engram creation (Reviewer output) | Full scan |
| Engram update (Reviewer patch) | Full scan of new content |
| Engram import (from file/URL) | Full scan |
| Engram promotion (draft -> candidate) | Full scan |
| Pre-injection (Selector picks engram) | Tool reference validation only (fast path) |

#### Interface

```python
class EngramScanner:
    def __init__(self, patterns_dir: Path = None):
        """Load scanning patterns from YAML pattern files."""

    def scan(self, engram: Engram) -> ScanVerdict:
        """Full scan of engram content and frontmatter."""

    def scan_tools(self, engram: Engram) -> ScanVerdict:
        """Fast path: validate only tool references against trust level."""

    def check_policy(self, trust: TrustLevel, results: list[ScanResult]) -> ScanVerdict:
        """Apply the policy matrix to scan results."""
```

---

### 5b. Engram Reviewer (Creation and Improvement)

The Reviewer is the component that creates new engrams and improves existing ones. It runs as a Claude Code subagent, forked from the main session.

#### Integration with Claude Code

The Reviewer integrates via two mechanisms:

1. **Stop hook**: When a session ends, a hook triggers the Reviewer to analyze the session.
2. **Explicit invocation**: User runs `/engram review` to trigger a review of the current session.
3. **Nudge-based** (optional): Every N tool calls (configurable, default 50), a background check evaluates whether a review is warranted. This is lower priority -- the Stop hook covers the common case.

**Hook configuration** (installed by the Engram package):

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "engram review --session=$SESSION_ID --mode=auto --non-blocking"
      }
    ]
  }
}
```

The `--non-blocking` flag means the hook forks a background process and returns immediately. The user is not blocked waiting for the review.

#### What Triggers a Review

| Trigger | Mechanism | Blocking? |
|---------|-----------|-----------|
| Session end | Stop hook | No (background fork) |
| User request | `/engram review` slash command | Yes (interactive) |
| Nudge (N tool calls) | PostToolUse hook counter | No (background fork) |
| Scheduled | Cron-like via `CronCreate` (daily) | No (background) |

#### Review Prompt Design

The Reviewer agent receives a structured prompt containing:

```
You are the Engram Reviewer. Analyze this session transcript for procedural
knowledge worth capturing.

SESSION CONTEXT:
- Project: {project_path}
- Session ID: {session_id}
- Duration: {duration}
- Tool calls: {tool_call_count}
- Outcome: {success|failure|partial|unknown}

EXISTING ENGRAMS (summaries only, for dedup):
{index of existing engrams with name + description + tags}

SESSION TRANSCRIPT (last N tool calls):
{filtered transcript: tool calls, results, user messages -- NOT full conversation}

TASK:
1. Identify any non-trivial procedures the agent discovered or refined.
   - Skip trivial actions (simple file reads, standard commands).
   - Skip anything that is already captured in an existing engram.
   - Focus on: multi-step procedures, error recovery, ordering dependencies,
     environment-specific workarounds, non-obvious tool usage patterns.

2. For each procedure found, decide:
   a) CREATE a new engram (if no similar engram exists)
   b) UPDATE an existing engram (if a similar one exists but this session
      reveals a better approach or additional context)
   c) SKIP (if the procedure is trivial or already well-captured)

3. Output your decisions as structured JSON:
{schema for reviewer output}
```

#### Reviewer Output Format

```json
{
  "decisions": [
    {
      "action": "create",
      "engram": {
        "name": "run-migrations-before-restart",
        "description": "Run Alembic migrations before restarting the app service",
        "triggers": { "tags": [...], "patterns": [...] },
        "body": "## When to Apply\n..."
      },
      "reason": "Agent discovered migration ordering dependency after deployment failure"
    },
    {
      "action": "update",
      "target": "existing-engram-slug",
      "patch": {
        "description": "updated description",
        "triggers": { "tags": ["added-tag"] },
        "body_patch": "## Additional Context\n..."
      },
      "reason": "Session revealed an additional failure mode not in the original"
    },
    {
      "action": "skip",
      "reason": "Standard pytest invocation, nothing novel"
    }
  ]
}
```

#### Update Strategy: Fuzzy Patching

When the Reviewer decides to update an existing engram, it does not rewrite the entire file. Instead, it produces a targeted patch:

1. **Append**: Add a new section to the body (e.g., "Additional Context").
2. **Replace section**: Replace a named markdown section (matched by heading).
3. **Frontmatter merge**: Merge new tags/patterns into existing triggers (union, not replace).
4. **Full rewrite**: Only when the Reviewer explicitly says the engram is fundamentally wrong.

The version number increments on every update. The previous version is copied to `versions/<slug>/v<N>.md`.

#### Non-Blocking Execution Model

```
Stop hook fires
  |
  v
engram review --session=$SID --mode=auto --non-blocking
  |
  v
Fork background process (Python subprocess, detached from terminal)
  |
  +-> Load session transcript from ~/.claude/projects/<project>/<session>.jsonl
  +-> Load engram index
  +-> Build reviewer prompt
  +-> Call Claude API (using claude_agent_sdk or anthropic SDK)
  +-> Parse structured output
  +-> For each "create" or "update" decision:
        +-> Build engram file
        +-> Run Scanner
        +-> If Scanner allows: atomic write to store
        +-> If Scanner blocks: log to ~/.claude/engrams/rejected.jsonl
  +-> Exit
```

The background process writes its own log to `~/.claude/engrams/review.log` for debugging.

---

### 5c. Engram Evaluator (Quality Tracking)

The Evaluator collects signals about engram effectiveness and computes quality scores.

#### Signals

| Signal | Source | Weight | How Captured |
|--------|--------|--------|-------------|
| **Usage** | Selector injected the engram | 0.0 (count only) | Selector logs to metrics JSONL |
| **Task success** | Session ended without errors / user expressed satisfaction | +0.3 | Stop hook, session outcome analysis |
| **User acceptance** | User did not override the engram's suggestion | +0.3 | PostToolUse hook: compare agent action vs engram suggestion |
| **User override** | User explicitly contradicted the engram | -0.5 | PostToolUse hook: detect "no, do X instead" pattern |
| **Explicit feedback** | User runs `/engram rate <slug> up|down` | +/-0.5 | CLI command |
| **Staleness** | Time since last use | -0.1/month | Lifecycle Manager periodic check |

#### Metrics Storage Format

Each engram has a JSONL sidecar at `metrics/<slug>.jsonl`. One line per event:

```jsonl
{"ts":"2026-03-28T14:20:00Z","event":"used","session":"abc123","context":"deploy task"}
{"ts":"2026-03-28T14:22:00Z","event":"success","session":"abc123"}
{"ts":"2026-03-29T09:10:00Z","event":"used","session":"def456","context":"restart service"}
{"ts":"2026-03-29T09:15:00Z","event":"override","session":"def456","detail":"user said skip migrations"}
{"ts":"2026-03-30T11:00:00Z","event":"feedback","rating":"up","session":"ghi789"}
```

#### Quality Score Computation

The quality score is a weighted rolling average over the last 30 events (or all events if fewer than 30):

```python
def compute_quality_score(events: list[MetricEvent]) -> float:
    """Compute quality score from recent metric events.

    Returns a float in [0.0, 1.0].
    """
    recent = events[-30:]  # rolling window

    if not recent:
        return 0.5  # prior: neutral

    usage_events = [e for e in recent if e.event == "used"]
    if not usage_events:
        return 0.5

    score = 0.5  # start at neutral
    for event in recent:
        if event.event == "success":
            score += 0.3 * (1 - score)    # diminishing returns toward 1.0
        elif event.event == "override":
            score -= 0.5 * score           # proportional penalty
        elif event.event == "feedback":
            if event.rating == "up":
                score += 0.5 * (1 - score)
            else:
                score -= 0.5 * score

    # Staleness decay
    if usage_events:
        days_since_use = (now() - usage_events[-1].ts).days
        if days_since_use > 30:
            months_stale = (days_since_use - 30) / 30
            score *= max(0.1, 1.0 - 0.1 * months_stale)

    return round(clamp(score, 0.0, 1.0), 3)
```

The computed score is written back into the engram's frontmatter `metrics.quality_score` field by the Lifecycle Manager.

#### Signal Capture Mechanism

Signals are captured via lightweight hooks that append to the JSONL sidecar:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "engram signal --event=tool_use --session=$SESSION_ID",
        "timeout": 1000
      }
    ]
  }
}
```

The `engram signal` command is fast (< 100ms target). It:
1. Checks if any engrams were injected this session (reads a session-scoped state file).
2. If yes, appends a `used` event to the relevant metrics JSONL.
3. Does NOT do quality computation (that is batch, not real-time).

---

### 5d. Engram Lifecycle Manager

The Lifecycle Manager handles state transitions, versioning, deduplication, and garbage collection.

#### State Machine

```
                create
                  |
                  v
              +-------+
              | draft |
              +---+---+
                  |
                  | auto-promote: usage_count >= 3 AND quality_score >= 0.5
                  | manual: /engram promote <slug>
                  v
           +-----------+
           | candidate |
           +-----+-----+
                 |
                 | auto-promote: usage_count >= 10 AND quality_score >= 0.7
                 |               AND streak >= 5
                 | manual: /engram promote <slug>
                 v
            +---------+
            | stable  |
            +----+----+
                 |
                 | auto-demote: quality_score < 0.3
                 | manual: /engram deprecate <slug>
                 v
          +------------+
          | deprecated |
          +-----+------+
                |
                | auto: 90 days in deprecated state with no usage
                | manual: /engram archive <slug>
                v
           +----------+
           | archived |
           +----------+
```

#### Transition Rules

| Transition | Auto Trigger | Manual Trigger |
|-----------|-------------|----------------|
| draft -> candidate | `usage_count >= 3 AND quality_score >= 0.5` | `/engram promote <slug>` |
| candidate -> stable | `usage_count >= 10 AND quality_score >= 0.7 AND streak >= 5` | `/engram promote <slug>` |
| stable -> deprecated | `quality_score < 0.3` | `/engram deprecate <slug>` |
| deprecated -> archived | `90 days deprecated AND usage_count_in_period == 0` | `/engram archive <slug>` |
| deprecated -> candidate | `quality_score >= 0.5` (re-evaluated after update) | `/engram promote <slug>` |
| any -> draft | -- | `/engram demote <slug>` (reset for rework) |

#### Version History and Rollback

Every update to an engram creates a version snapshot:

```
versions/run-migrations-before-restart/
  v1.md      # original creation
  v2.md      # first update
  v3.md      # second update (current version is v3 in engram/)
```

Rollback: `/engram rollback <slug> <version>` copies the specified version back to `engram/<slug>.md`, increments the version counter (so v3 rolled back to v1 content becomes v4), and resets the quality score to 0.5.

#### Deduplication Detection

When the Reviewer proposes a new engram, the Lifecycle Manager checks for duplicates:

1. **Exact name match**: Reject if slug already exists (Reviewer should have caught this).
2. **Tag overlap**: If a new engram shares >= 60% of tags with an existing engram, flag for manual review.
3. **Description similarity**: Compute Jaccard similarity on description tokens. If >= 0.7, flag.
4. **LLM judgment** (optional, expensive): Ask Claude to compare the new engram body with the top-3 similar existing engrams and decide: duplicate, complementary, or distinct.

Flagged duplicates are written to `draft` state with a `_dedup_candidates` field in their frontmatter listing the similar engrams. The user can resolve via `/engram dedup <slug>`.

#### Interface

```python
class LifecycleManager:
    def __init__(self, store: EngramStore):
        ...

    def check_transitions(self) -> list[TransitionProposal]:
        """Scan all engrams and propose state transitions based on metrics."""

    def apply_transition(self, slug: str, target_state: State, reason: str) -> Engram:
        """Transition an engram to a new state. Creates version snapshot."""

    def check_duplicates(self, engram: Engram) -> list[DedupCandidate]:
        """Find potential duplicates for a proposed engram."""

    def run_gc(self) -> GCReport:
        """Archive stale deprecated engrams. Clean up orphaned metrics files."""

    def rollback(self, slug: str, version: int) -> Engram:
        """Rollback an engram to a previous version."""
```

---

### 5e. Engram Selector (Relevance Matching)

The Selector runs at session start and (optionally) at each turn to find engrams relevant to the current context. It is the performance-critical path -- it must be fast because it runs inline with the user's request.

#### Matching Algorithm

The Selector uses a multi-stage pipeline, cheapest filters first:

```
Stage 1: State Filter (O(1) per engram)
  - Only candidate and stable engrams are eligible
  - Skip draft, deprecated, archived

Stage 2: Project Filter (O(1) per engram)
  - If engram has triggers.projects, check if current project path matches any glob
  - Engrams with empty triggers.projects match all projects

Stage 3: File Filter (O(F) where F = files in context)
  - If engram has triggers.files, check if any file in the current session context
    matches any glob
  - Engrams with empty triggers.files pass this stage

Stage 4: Tag Match (O(T) where T = tags)
  - Score = |intersection(engram.tags, context_tags)| / |engram.tags|
  - context_tags extracted from: project name, recent file paths, user prompt keywords
  - Minimum threshold: score >= 0.3

Stage 5: Pattern Match (O(P) where P = patterns)
  - Run each engram's trigger patterns against the user's current prompt
  - Any match = eligible

Stage 6: Ranking
  - Score = (tag_match_score * 0.4) + (quality_score * 0.3) + (state_bonus * 0.2)
            + (recency_bonus * 0.1)
  - state_bonus: stable=1.0, candidate=0.5
  - recency_bonus: 1.0 if used in last 7 days, 0.5 if last 30, 0.0 otherwise
  - Sort descending
```

#### Token Budget Management

Engrams injected into context consume tokens. The Selector enforces a budget:

| Priority | Budget | Description |
|----------|--------|-------------|
| High confidence (score >= 0.8) | Up to 2000 tokens total | Top-ranked engrams |
| Medium confidence (0.5-0.8) | Up to 1000 tokens total | Supporting engrams |
| Low confidence (0.3-0.5) | Up to 500 tokens total | Mentioned as available, not fully injected |

**Total budget**: 3500 tokens maximum. This is approximately 5-8 engrams depending on length.

If the budget is exceeded, lower-ranked engrams are summarized (description only, not full body) or dropped entirely.

#### Injection Format

Matched engrams are injected into the session context as a structured block:

```markdown
## Active Engrams

The following procedural knowledge is relevant to this session:

### [stable] Run Migrations Before Restart (v3, score: 0.82)
When deploying changes that include Alembic migrations, run `alembic upgrade head`
before restarting the service. The healthcheck queries tables that may not exist
until migrations complete.

### [candidate] Pytest Fixture Scoping (v1, score: 0.65)
[Summary only -- full engram available via /engram view pytest-fixture-scoping]
```

#### Interface

```python
class EngramSelector:
    def __init__(self, store: EngramStore, token_budget: int = 3500):
        ...

    def select(self, context: SessionContext) -> list[ScoredEngram]:
        """Return ranked engrams matching the current context, within token budget."""

    def format_injection(self, engrams: list[ScoredEngram]) -> str:
        """Format selected engrams for injection into session context."""
```

---

### 5f. Engram CLI / User Interface

Users interact with Engram through two complementary interfaces: a CLI tool (`engram`) and a Claude Code skill (`/engram`).

#### CLI Commands

```
engram list [--state=STATE] [--trust=LEVEL] [--tag=TAG] [--project=PATH]
    List engrams with optional filters. Default: show candidate + stable.

engram view <slug>
    Display the full engram file with rendered frontmatter.

engram rate <slug> up|down
    Record explicit user feedback. Updates metrics JSONL.

engram promote <slug>
    Manually promote an engram to the next lifecycle state.

engram deprecate <slug>
    Move an engram to deprecated state.

engram archive <slug>
    Move an engram to archived state. Moves file to archive/ directory.

engram demote <slug>
    Reset an engram to draft state for rework.

engram rollback <slug> <version>
    Rollback an engram to a previous version.

engram dedup <slug>
    Show deduplication candidates and resolve interactively.

engram import <file|url>
    Import an engram from an external source. Full security scan applied.

engram export <slug> [--output=PATH]
    Export an engram to a standalone file.

engram review [--session=ID]
    Trigger the Reviewer on the current or specified session.

engram stats [--slug=SLUG]
    Show quality metrics, usage stats, and lifecycle history.

engram rebuild-index
    Force rebuild of index.json from engram files.

engram scan <slug|file>
    Run the security scanner on an engram and show results.

engram gc
    Run garbage collection: archive stale deprecated engrams, clean orphans.
```

#### Claude Code Skill Integration

The `/engram` skill wraps the CLI with conversational UI:

```yaml
---
name: engram
description: "Manage procedural memory engrams. Create, review, rate, and browse learned procedures."
allowed-tools: ["Bash", "Read", "Write", "Edit"]
---
```

**Slash command routing:**

| User Types | Action |
|-----------|--------|
| `/engram` | Show summary: N engrams by state, top 5 by quality |
| `/engram list` | Run `engram list` and format output |
| `/engram review` | Fork Reviewer agent on current session |
| `/engram rate <slug> up` | Run `engram rate`, confirm to user |
| `/engram view <slug>` | Display engram with formatted frontmatter |
| `/engram promote <slug>` | Run promotion, show before/after state |

#### Feedback Mechanism

After an engram is injected and the task completes, the Stop hook includes a feedback prompt:

```
Engrams used this session:
  - run-migrations-before-restart (stable, v3)
  - pytest-fixture-scoping (candidate, v1)

Rate them? Use /engram rate <slug> up|down
```

This is a non-blocking suggestion in the session end output, not an interactive prompt.


## 6. Integration Architecture

Engram integrates with Claude Code through four touchpoints, each mapping to an existing extension mechanism:

### Integration Map

| Claude Code Mechanism | Engram Component | Purpose |
|-----------------------|-----------------|---------|
| **Skill** (`~/.claude/skills/engram/SKILL.md`) | CLI / User Interface | User-facing commands for engram management |
| **Agent** (`~/.claude/agents/engram-reviewer.md`) | Reviewer | Subagent definition for session review |
| **Hooks** (`settings.json` hooks section) | Signal Collector + Reviewer trigger | Capture usage signals, trigger reviews |
| **Memory relationship** | Selector output injected alongside MEMORY.md | Procedural memory complements declarative memory |

### Hook Configuration (installed by `engram install`)

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "engram review --session=$CLAUDE_SESSION_ID --mode=auto --non-blocking",
        "timeout": 5000
      },
      {
        "type": "command",
        "command": "engram signal --event=session_end --session=$CLAUDE_SESSION_ID",
        "timeout": 1000
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": "engram signal --event=tool_use --session=$CLAUDE_SESSION_ID --tool=$TOOL_NAME",
        "timeout": 1000
      }
    ],
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "engram select --session=$CLAUDE_SESSION_ID --prompt-file=$PROMPT_FILE --output=$ENGRAM_CONTEXT_FILE",
        "timeout": 2000
      }
    ]
  }
}
```

### Skill File

```markdown
---
name: engram
description: "Manage procedural memory engrams. Create, review, rate, and browse learned procedures."
allowed-tools: ["Bash", "Read", "Write", "Edit"]
---

# Engram - Procedural Memory Manager

Manage the agent's learned procedural knowledge.

## Commands

- `/engram` - Show engram dashboard (count by state, top by quality)
- `/engram list` - List engrams with filters
- `/engram view <slug>` - View full engram
- `/engram review` - Review current session for new engrams
- `/engram rate <slug> up|down` - Rate an engram
- `/engram promote <slug>` - Promote to next state
- `/engram deprecate <slug>` - Deprecate an engram
- `/engram stats` - Show quality metrics

## Usage

Run the corresponding `engram` CLI command via Bash and present results to the user.
```

### Agent Definition

```markdown
---
name: Engram Reviewer
description: Analyzes session transcripts to discover and capture procedural knowledge as engrams.
color: emerald
emoji: brain
vibe: Learns from experience. Every session is a chance to get better.
---

# Engram Reviewer Agent

You review Claude Code session transcripts to find procedural knowledge worth
capturing as engrams.

## Your Task

You receive a session transcript and an index of existing engrams. You must:

1. Identify non-trivial procedures discovered during the session.
2. For each, decide: CREATE new engram, UPDATE existing engram, or SKIP.
3. Output structured JSON following the reviewer output schema.

## What Makes Good Procedural Knowledge

- Multi-step procedures where ordering matters
- Error recovery patterns (what to do when X fails)
- Environment-specific workarounds
- Non-obvious tool usage patterns
- Dependency relationships (do A before B because C)

## What to Skip

- Simple file reads or standard commands
- Anything already captured in an existing engram
- Declarative facts (those belong in MEMORY.md)
- One-off debugging steps unlikely to recur
```

### Relationship to Existing Memory

```
CLAUDE.md (always loaded)
  |
  +-- Instructions, rules, global config
  |
MEMORY.md (always loaded, per-project)
  |
  +-- Declarative facts: "uses Python 3.12", "deploy to AWS ECS"
  |
Engrams (selectively loaded, per-session)
  |
  +-- Procedural knowledge: "when deploying, run migrations first"
  +-- Only relevant engrams injected (Selector decides)
  +-- Budget-limited to avoid context bloat
```

Engrams are NOT loaded into `MEMORY.md`. They have their own injection point in the context assembly, after memory and before the user's prompt. This keeps the concerns separated and allows independent lifecycle management.


## 7. Security Architecture

### Threat Model

| Threat | Vector | Mitigation |
|--------|--------|------------|
| Credential exfiltration | Agent-created engram contains API keys from session | Scanner: credential pattern matching |
| Command injection | Engram body contains `$(malicious)` shell expansion | Scanner: shell injection patterns |
| Prompt injection | Imported engram contains "ignore previous instructions" | Scanner: prompt injection patterns |
| Privilege escalation | Agent-created engram references restricted tools | Trust-level tool allowlists |
| Data exfiltration | Engram triggers `curl` to send data externally | Scanner: network patterns + tool restrictions |
| Stale/wrong advice | Deprecated engram gives harmful guidance | Lifecycle Manager: auto-deprecation, quality decay |

### Scanning Pipeline (Detail)

```
Input engram
  |
  v
[1. Frontmatter Validation]
  - All required fields present and typed correctly
  - State is valid enum value
  - Trust level is valid
  - allowed_tools only contains recognized tool names
  |
  v
[2. Tool Reference Audit]
  - Extract all tool references from body (Bash, Edit, Write, etc.)
  - Compare against engram's allowed_tools list
  - Compare against trust-level tool allowlist
  - BLOCK if engram references tools it is not allowed to use
  |
  v
[3. Content Pattern Scan]
  - Run all 150+ regex patterns against body text
  - Categorize each match by severity
  - Apply policy matrix: (trust_level, max_severity) -> action
  |
  v
[4. Structural Analysis]
  - Check for unusually long lines (> 500 chars) -- potential obfuscation
  - Check for base64 blobs (> 50 chars of base64)
  - Check for unicode homoglyphs in tool names
  |
  v
Output: ScanVerdict {action: allow|warn|block, results: [...]}
```

### Tool Allowlists by Trust Level

| Trust Level | Allowed Tools |
|-------------|--------------|
| `system` | All tools |
| `verified` | All tools the user has granted to the skill |
| `community` | `Read`, `Grep`, `Glob` (read-only) |
| `agent-created` | `Read`, `Grep`, `Glob`, `Edit` (read + edit, no Bash by default) |

An `agent-created` engram can reference Bash commands in its body text (as documentation), but the Scanner will flag them as `warning` severity. The engram must be promoted to `verified` to have Bash in its `allowed_tools`.

### Promotion Security Requirements

| Transition | Security Requirement |
|-----------|---------------------|
| draft -> candidate | Full scan passes with no `critical` findings |
| candidate -> stable | Full scan passes with no `warning` or `critical` findings |
| any -> verified (trust upgrade) | User manually reviews and approves via `/engram verify <slug>` |
| import (any source) | Full scan + user confirmation prompt |


## 8. Implementation as a Python Package

### Package Structure

```
engram/
  pyproject.toml
  LICENSE                       # MIT
  README.md
  src/
    engram/
      __init__.py               # Version, public API
      cli.py                    # Click-based CLI (engram command)
      models.py                 # Pydantic models: Engram, ScanResult, MetricEvent, etc.
      store.py                  # EngramStore: read/write/lock/index operations
      scanner.py                # EngramScanner: security scanning
      reviewer.py               # EngramReviewer: session analysis, engram creation
      evaluator.py              # EngramEvaluator: quality score computation
      lifecycle.py              # LifecycleManager: state transitions, dedup, GC
      selector.py               # EngramSelector: relevance matching, injection
      hooks.py                  # Hook scripts: signal capture, review triggers
      patterns/                 # Security scanning pattern files
        credentials.yaml
        injection.yaml
        filesystem.yaml
        network.yaml
        obfuscation.yaml
        prompt_injection.yaml
      templates/                # Engram file templates
        engram.md.j2
        skill.md.j2             # For engram-to-skill promotion
      claude/                   # Claude Code integration files
        skills/
          engram/
            SKILL.md
        agents/
          engram-reviewer.md
  tests/
    conftest.py
    test_models.py
    test_store.py
    test_scanner.py
    test_reviewer.py
    test_evaluator.py
    test_lifecycle.py
    test_selector.py
    test_hooks.py
    test_cli.py
    fixtures/
      sample_engrams/
      sample_sessions/
      sample_patterns/
```

### pyproject.toml

```toml
[project]
name = "engram"
version = "0.1.0"
description = "Self-improving procedural memory for Claude Code"
license = "MIT"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "python-frontmatter>=1.1",
    "filelock>=3.12",
    "jinja2>=3.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
]
llm = [
    "anthropic>=0.30",
]

[project.scripts]
engram = "engram.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Installation and Setup

```bash
# Install the package
pip install engram
# or
uv pip install engram

# Install Claude Code integration (hooks, skill, agent)
engram install

# This command:
# 1. Copies skill to ~/.claude/skills/engram/SKILL.md
# 2. Copies agent to ~/.claude/agents/engram-reviewer.md
# 3. Creates ~/.claude/engrams/ directory structure
# 4. Merges hook configuration into the project's .claude/settings.json
#    (or ~/.claude/settings.json for global install)
# 5. Prints confirmation and next steps
```

### Key Implementation Notes

1. **No runtime LLM dependency for core operations.** The `anthropic` SDK is an optional dependency (`pip install engram[llm]`). All operations except the Reviewer work without it. The Reviewer can also be run as a Claude Code subagent (via the agent definition), which uses the user's existing Claude Code session -- no separate API key needed.

2. **Pydantic for all data models.** Engram frontmatter is validated with Pydantic on read and write. Invalid engrams fail loudly at parse time, not at use time.

3. **Click for CLI.** Standard Python CLI framework. Each subcommand maps to a component method.

4. **filelock for atomic writes.** Cross-platform advisory file locking. The `store.lock` file ensures only one process writes at a time.

5. **YAML patterns for scanner.** Security patterns are stored as YAML files, not hardcoded. This makes them easy to update, extend, and review.


## 9. Key Design Decisions

### ADR-001: File-Based Storage Over SQLite

**Decision**: Store engrams as individual Markdown files with YAML frontmatter, not in a SQLite database.

**Alternatives considered**:
- SQLite: Better query performance, atomic transactions, single file.
- JSON files: Simpler parsing, but less human-readable.

**Why this choice**: Engram files must be human-readable, version-controllable, and editable with any text editor. They must work within Claude Code's existing file-based ecosystem (SKILL.md, MEMORY.md, CLAUDE.md). The performance cost is negligible at the expected scale (tens to hundreds of engrams, not thousands). The index file provides adequate query performance without a database.

**What we give up**: Complex queries, referential integrity, concurrent write performance beyond file locking.

---

### ADR-002: JSONL Sidecars for Metrics Over Embedded Frontmatter

**Decision**: Store detailed metric events in separate JSONL files. Only the computed quality score lives in the engram frontmatter.

**Alternatives considered**:
- All metrics in frontmatter: Simpler, single file per engram.
- SQLite metrics table: Better query performance for aggregate analysis.

**Why this choice**: Metric events are append-heavy (every usage adds a line). Rewriting the entire engram file on every tool call would be expensive and risky (corrupting the engram body). JSONL allows cheap appends. The frontmatter holds only the computed score, which is updated periodically by the Lifecycle Manager, not on every event.

**What we give up**: Atomic consistency between the engram file and its metrics (they can briefly disagree). The quality score in frontmatter may lag behind the latest events.

---

### ADR-003: Background Reviewer Over Inline Analysis

**Decision**: The Reviewer runs as a background process (forked on session end), not inline during the session.

**Alternatives considered**:
- Inline analysis: Review after every N tool calls, blocking the session.
- Hybrid: Lightweight inline check + heavy background analysis.

**Why this choice**: The Reviewer must call an LLM to analyze the session transcript. This takes seconds to minutes. Blocking the user's session for engram creation is unacceptable -- it breaks flow. Background processing means the user's session ends normally and engrams appear asynchronously.

**What we give up**: Immediate feedback. The user does not see newly created engrams until their next session. The Reviewer may analyze a partial context if the session transcript is very long.

---

### ADR-004: Extending SKILL.md Format Over Custom Format

**Decision**: The engram file format extends Claude Code's existing SKILL.md YAML frontmatter rather than inventing a wholly new format.

**Alternatives considered**:
- Custom TOML format: More expressive for nested data.
- Pure JSON: Easier to parse programmatically.
- Completely new Markdown schema.

**Why this choice**: Compatibility. An engram that gets promoted to a skill should require minimal transformation. Users already understand the SKILL.md frontmatter convention. Tools that parse SKILL.md can be taught to parse engrams with minimal changes. The additional fields (metrics, lineage, triggers) are additive -- they don't conflict with the base SKILL.md schema.

**What we give up**: YAML frontmatter is not ideal for deeply nested data (the triggers object gets verbose). But the nesting depth is bounded and manageable.

---

### ADR-005: Two-Store Resolution (Global + Project) Over Single Store

**Decision**: Engrams live in two locations -- `~/.claude/engrams/` (global) and `<project>/.engram/` (project-level) -- with project-level taking priority.

**Alternatives considered**:
- Single global store with project tags for filtering.
- Project-only storage with explicit export for sharing.

**Why this choice**: Some procedures are universal ("always run tests before committing") and some are project-specific ("this project needs migrations before restart"). Two stores mirror Claude Code's own pattern: global `~/.claude/` for cross-project config and project-level `.claude/` for project-specific config. Project-level engrams can be committed to the repo and shared with the team.

**What we give up**: Complexity in the Selector (must merge two indexes). Potential confusion about which store an engram lives in. Slug collisions between stores (resolved by project-level winning).

---

### ADR-006: Conservative Auto-Promotion Thresholds

**Decision**: Require `usage_count >= 10 AND quality_score >= 0.7 AND streak >= 5` for promotion to stable.

**Alternatives considered**:
- Lower thresholds (usage >= 3, score >= 0.5) for faster promotion.
- No auto-promotion (all promotions manual).

**Why this choice**: False positives in stable engrams are worse than slow promotion. A stable engram is injected with high priority and trusted implicitly. If it gives bad advice, the damage compounds across sessions. Conservative thresholds ensure an engram has proven itself across multiple sessions and contexts before reaching stable. Draft and candidate engrams still get used -- they just have lower injection priority.

**What we give up**: Slow ramp-up time. New engrams take many sessions to reach stable. This is intentional -- procedural knowledge should be battle-tested.

---

### ADR-007: Token Budget for Engram Injection

**Decision**: Hard cap of 3500 tokens for all injected engrams per session, with tiered allocation by confidence.

**Alternatives considered**:
- No budget (inject all matching engrams): Risk of context bloat.
- Dynamic budget based on model context window size.
- User-configurable budget.

**Why this choice**: Context window space is the scarcest resource in an LLM session. Every token spent on engrams is a token not available for the user's actual task. 3500 tokens (~5-8 engrams) provides meaningful guidance without crowding the context. The tiered system ensures high-confidence engrams get full representation while lower-confidence ones get summaries.

**What we give up**: Completeness. If 20 engrams match, only the top 5-8 are injected. The user can manually access others via `/engram view`. A future version could make the budget configurable.

---

### ADR-008: Optional LLM Dependency

**Decision**: The `anthropic` SDK is an optional dependency. Core engram operations (store, scan, select, evaluate, lifecycle) work without any LLM calls. Only the Reviewer requires LLM access.

**Alternatives considered**:
- Required LLM dependency for all components (richer analysis everywhere).
- No LLM dependency at all (rule-based Reviewer).

**Why this choice**: The Reviewer is the only component that fundamentally needs LLM judgment (analyzing a session transcript to decide what to capture). Everything else can work with deterministic logic. Making the LLM optional means: (a) users can use engrams without a separate API key (the Reviewer runs as a Claude Code subagent using the existing session), (b) tests run without API calls, (c) CI/CD pipelines can use engram tooling without LLM credentials.

**What we give up**: Richer deduplication (LLM-based similarity is better than Jaccard on tokens). Smarter quality evaluation (LLM could judge engram relevance, not just count successes). These can be added as optional enhancements later.
