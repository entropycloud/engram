---
Status: Active
Created: 2026-04-01
Updated: 2026-04-01
Related: ../../designs/engram-architecture.md
Tags: implementation, phases, milestones
---

# Engram Implementation Plan

Phased implementation plan for the Engram procedural memory system. Each phase produces a shippable increment. An implementer agent should be able to pick up any task and know exactly what to build.

Reference: [Engram Architecture](../../designs/engram-architecture.md)


## Phase 0: Project Scaffolding

### Goal
A working Python package skeleton that installs, lints, type-checks, and runs an empty test suite. CI/CD pipeline green on every push.

### Tasks

1. **`pyproject.toml`** — Create at repo root. Dependencies: `click>=8.1`, `pyyaml>=6.0`, `pydantic>=2.0`, `python-frontmatter>=1.1`, `filelock>=3.12`, `jinja2>=3.1`. Dev extras: `pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.4`, `mypy>=1.10`. LLM extras: `anthropic>=0.30`. Build backend: `hatchling`. Entry point: `engram = "engram.cli:main"`.

2. **`src/engram/__init__.py`** — Export `__version__ = "0.1.0dev"`.

3. **`src/engram/cli.py`** — Stub: Click group `main` with `--version` flag.

4. **`tests/conftest.py`** — Shared fixtures: `tmp_store` (tmp_path-based engram store directory with full subdirectory structure).

5. **`tests/fixtures/sample_engrams/run-migrations-before-restart.md`** — Example engram from architecture doc Section 3.

6. **`.github/workflows/ci.yml`** — Python 3.11 + 3.12 matrix. Steps: checkout, install, ruff, mypy, pytest.

7. **`ruff.toml`** — Target Python 3.11. Rules: `E`, `F`, `W`, `I`, `UP`, `B`, `SIM`. Line length 100.

8. **`LICENSE`** — MIT license.

9. **`README.md`** — One-paragraph description + "Under construction".

### Dependencies
None.

### Acceptance Criteria
- `pip install -e ".[dev]"` succeeds
- `engram --version` outputs `0.1.0dev`
- `ruff check`, `mypy`, and `pytest` all pass
- GitHub Actions CI green


## Phase 1: Core Data Layer

### Goal
Pydantic models for all data types, working EngramStore with atomic read/write/lock/index, CLI `list` and `view` commands.

### Tasks

1. **`src/engram/models.py`** — Pydantic v2 models: `TrustLevel`, `EngramState`, `Triggers`, `Metrics`, `Lineage`, `Engram`, `MetricEvent`, `IndexEntry`, `StoreIndex`, `ScanResult`, `ScanVerdict`. Full schema from architecture doc Section 3.

2. **`src/engram/store.py`** — `EngramStore` class: `read()`, `write()` (atomic: lock → tmp → fsync → rename → rebuild index → unlock), `delete()`, `list()`, `read_index()`, `rebuild_index()`, `move_to_archive()`, `save_version()`, `get_version()`, `list_versions()`, `cleanup_tmp_files()`. `MultiStore` class: two-store resolution (project wins on slug collision).

3. **`src/engram/formatting.py`** — CLI output helpers: `format_engram_table()`, `format_engram_detail()`.

4. **`src/engram/cli.py`** — Add: `engram list [--state --tag]`, `engram view <slug>`, `engram rebuild-index`.

### Tests
- Model roundtrip serialization, validation rejection of invalid data
- Store write+read roundtrip, index rebuild, archive, version history
- Lock contention with threads
- MultiStore merge with slug collision
- CLI list and view against populated store

### Acceptance Criteria
- All models validate correctly, reject invalid data
- Store atomic writes survive simulated crashes
- MultiStore merges global + project stores correctly
- >90% coverage on models.py and store.py


## Phase 2: Security Scanner

### Goal
150+ pattern security scanner with trust-level policies. CLI `engram scan` command.

### Tasks

1. **`src/engram/patterns/*.yaml`** — 7 pattern files adapted from Hermes skills_guard.py:
   - `credentials.yaml` (~40), `injection.yaml` (~30), `filesystem.yaml` (~25), `network.yaml` (~20), `obfuscation.yaml` (~15), `prompt_injection.yaml` (~15), `tool_abuse.yaml` (~10)
   - Each pattern: `id`, `pattern` (regex), `severity`, `category`, `description`

2. **`src/engram/scanner.py`** — `EngramScanner`: 4-stage pipeline (frontmatter validation → tool reference audit → content pattern scan → structural analysis). Policy matrix: `(trust, severity) → allow|warn|block`. Fast path `scan_tools()` for pre-injection.

3. **`src/engram/cli.py`** — Add: `engram scan <slug|file>`.

### Tests
- Clean engram → allow. AWS key → block. Shell injection → block.
- Policy matrix coverage for all trust×severity combinations
- Fast path only checks tools, not body patterns
- All 7 pattern files load without errors

### Acceptance Criteria
- 150+ patterns load and match correctly
- Policy matrix enforced per architecture doc
- `engram scan` prints results and exits with correct code


## Phase 3: Lifecycle Manager

### Goal
State machine, version history, rollback, auto-promotion/demotion, deduplication, GC.

### Tasks

1. **`src/engram/lifecycle.py`** — `LifecycleManager`: `check_transitions()` (auto-promotion thresholds), `apply_transition()` (validate + scan + version snapshot + write), `check_duplicates()` (tag overlap ≥0.6, Jaccard ≥0.7), `run_gc()` (archive stale deprecated, clean orphans), `rollback()`.

2. **`src/engram/models.py`** — Add: `TransitionProposal`, `DedupCandidate`, `GCReport`.

3. **`src/engram/cli.py`** — Add: `promote`, `deprecate`, `archive`, `demote`, `rollback`, `gc`, `dedup`.

### Tests
- Auto-transition detection for all threshold rules
- Illegal transition rejection (draft → stable directly)
- Promotion blocked by scanner findings
- Rollback produces incremented version with old content
- Dedup detects tag overlap and description similarity
- GC archives stale engrams, cleans orphans

### Acceptance Criteria
- State machine enforces all legal transitions
- Scanner integration blocks unsafe promotions
- Version history preserves all changes


## Phase 4: Evaluator and Signal Collection

### Goal
JSONL metrics sidecars, quality score computation, signal capture hooks, CLI `stats` and `rate`.

### Tasks

1. **`src/engram/evaluator.py`** — `EngramEvaluator`: `append_event()`, `read_events()`, `compute_quality_score()` (rolling window of 30, weights: +0.3 success, -0.5 override, ±0.5 feedback, staleness decay after 30 days), `update_engram_score()`, `update_all_scores()`.

2. **`src/engram/hooks.py`** — `record_signal()`, `record_session_end()`, `record_feedback()`. Performance target: <100ms.

3. **`src/engram/cli.py`** — Add: `engram stats [--slug]`, `engram rate <slug> up|down`, `engram signal`.

### Tests
- Quality score trends toward 1.0 with only successes, 0.0 with only overrides
- Staleness decay applies after 30 days
- Rolling window limits to last 30 events
- Signal recording completes in <200ms (relaxed for tests)

### Acceptance Criteria
- Quality score computation matches architecture doc algorithm exactly
- JSONL sidecars are append-safe
- `engram rate` immediately updates quality score


## Phase 5: Selector (Relevance Matching)

### Goal
Multi-stage matching pipeline, token budget, context injection formatting, `UserPromptSubmit` hook integration.

### Tasks

1. **`src/engram/selector.py`** — `EngramSelector`: 6-stage pipeline (state → project → file → tag → pattern → ranking). Token budget: 3500 total (2000 high, 1000 medium, 500 low). `format_injection()` for markdown output. `run_pre_injection_scan()`.

2. **`src/engram/models.py`** — Add: `SessionContext`, `ScoredEngram`.

3. **`src/engram/cli.py`** — Add: `engram select --prompt-file --project --output`.

4. **`src/engram/hooks.py`** — Session state writer for downstream signal attribution.

### Tests
- State filter excludes draft/archived
- Project/file glob matching
- Tag scoring with threshold
- Pattern regex matching against prompt
- Ranking formula produces correct ordering
- Token budget enforced with summarization fallback
- End-to-end: 5 engrams, relevant prompt, correct top results

### Acceptance Criteria
- Pipeline filters and ranks correctly
- Budget enforced, low-confidence engrams get summaries only
- `engram select` works as hook entry point


## Phase 6: Reviewer (Session Analysis)

### Goal
Session transcript analysis, engram creation/patching, non-blocking background execution, Claude Code agent definition.

### Tasks

1. **`src/engram/reviewer.py`** — `EngramReviewer`: `build_review_prompt()`, `parse_review_output()`, `execute_decisions()`, `review_session()`, `load_transcript()`, `filter_transcript()`.

2. **`src/engram/fuzzy_patch.py`** — Patching engine: `append`, `replace_section`, `frontmatter_merge`, `full_rewrite`.

3. **`src/engram/models.py`** — Add: `ReviewDecision`, `ReviewOutput`, `ReviewReport`.

4. **`src/engram/templates/engram.md.j2`** — Jinja2 template for new engram files.

5. **`src/engram/cli.py`** — Add: `engram review [--session --mode --non-blocking]`.

### Tests
- Prompt construction includes session context and engram index
- JSON output parsing for create/update/skip decisions
- Create decision writes draft engram with agent-created trust
- Scanner blocks unsafe reviewer output → logged to rejected.jsonl
- Fuzzy patch: append, replace_section, frontmatter_merge, full_rewrite
- Non-blocking mode forks and returns immediately

### Acceptance Criteria
- Reviewer creates well-formed engrams from session analysis
- Fuzzy patching handles all four patch types
- Scanner integration prevents unsafe engrams
- Non-blocking mode works reliably


## Phase 7: Claude Code Integration

### Goal
One-command setup: `engram install` configures skill, agent, and hooks.

### Tasks

1. **`src/engram/claude/skills/engram/SKILL.md`** — Skill file for `/engram` commands.

2. **`src/engram/claude/agents/engram-reviewer.md`** — Agent definition for reviewer subagent.

3. **`src/engram/install.py`** — `install_claude_code_integration()`: copy skill, agent, create store dirs, merge hooks into settings.json. `uninstall_claude_code_integration()`: remove without deleting engram data.

4. **`src/engram/cli.py`** — Add: `engram install [--global --project]`, `engram uninstall`.

### Tests
- Install creates files at correct paths
- Hook merge doesn't overwrite existing hooks
- Idempotent: double install doesn't duplicate
- Uninstall preserves engram store data

### Acceptance Criteria
- `engram install` sets up everything in one command
- All four hook touchpoints work end-to-end
- `engram uninstall` cleanly removes integration


## Phase 8: Polish and Release

### Goal
Production-ready v0.1.0.

### Tasks
1. Complete README with examples and architecture overview
2. Test coverage ≥90% on core modules, ≥80% on reviewer, ≥70% on CLI/hooks
3. Edge cases: empty store, corrupted files, concurrent writes, large engrams, Unicode
4. Performance: signal <100ms, select <200ms, index rebuild <500ms, scan <200ms
5. Version bump to `0.1.0`, tag `v0.1.0`

### Acceptance Criteria
- All tests pass, coverage targets met
- Performance targets met for 100 engrams
- `pip install .` works in clean Python 3.11+ venv
- `engram install && engram --help` works end-to-end


## Implementation Notes

<!-- Append-only. Add dated notes as implementation progresses. -->
