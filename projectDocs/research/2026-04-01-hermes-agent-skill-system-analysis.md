---
Status: Complete
Created: 2026-04-01
Tags: hermes, skills, self-improvement, prior-art
---

# Hermes Agent Skill System Analysis

## 1. Executive Summary

NousResearch/hermes-agent is an open-source AI agent framework built by Nous Research that includes a self-improving skill system. Skills are the agent's "procedural memory" -- reusable, markdown-based instruction sets that capture how to perform specific task types. The agent can create new skills autonomously after completing complex tasks, update existing skills when they prove outdated, and install community-contributed skills from a hub with security scanning. We are studying this system because it is the most complete open-source implementation of agent self-improvement via skill creation, and its design decisions (both strengths and gaps) directly inform our Engram architecture.

## 2. Skill System Architecture

### 2.1 Skill Format

Each skill is a directory containing a `SKILL.md` file with YAML frontmatter and markdown body, plus optional supporting files in designated subdirectories.

**Directory layout** (from `tools/skill_manager_tool.py`, lines 22-32):

```
~/.hermes/skills/
├── my-skill/
│   ├── SKILL.md
│   ├── references/
│   ├── templates/
│   ├── scripts/
│   └── assets/
└── category-name/
    └── another-skill/
        └── SKILL.md
```

**SKILL.md format** (from an actual bundled skill, `skills/gaming/minecraft-modpack-server/SKILL.md`):

```yaml
---
name: minecraft-modpack-server
description: Set up a modded Minecraft server from a CurseForge/Modrinth server pack zip.
tags: [minecraft, gaming, server, neoforge, forge, modpack]
---
```

The body follows a consistent structure: "When to use" trigger conditions, numbered procedural steps with exact commands, and pitfall/verification sections. The frontmatter supports optional fields including `platforms` (for OS-specific skills) and `metadata.hermes` (for conditional activation rules like `fallback_for_toolsets`, `requires_toolsets`, `fallback_for_tools`, `requires_tools`).

The repo ships with **74 bundled skills** across 26 categories (apple, autonomous-ai-agents, creative, data-science, devops, diagramming, domain, email, feeds, gaming, gifs, github, inference-sh, leisure, mcp, media, mlops, note-taking, productivity, red-teaming, research, smart-home, social-media, software-development, and more).

### 2.2 Skill Manager Tool

The skill manager (`tools/skill_manager_tool.py`, 743 lines) is exposed to the agent as a function-calling tool named `skill_manage` with six actions:

| Action | Purpose |
|--------|---------|
| `create` | Create a new skill (SKILL.md + directory structure) |
| `edit` | Full rewrite of an existing skill's SKILL.md |
| `patch` | Targeted find-and-replace within SKILL.md or supporting files |
| `delete` | Remove a skill entirely |
| `write_file` | Add/overwrite a supporting file (reference, template, script, asset) |
| `remove_file` | Remove a supporting file from a skill |

### 2.3 Validation Constraints

The tool enforces the following validation rules (from `skill_manager_tool.py`, lines 83-195):

- **Name**: Regex `^[a-z0-9][a-z0-9._-]*$`, max 64 characters. Must start with a letter or digit, allows lowercase letters, numbers, hyphens, dots, and underscores.
- **Category**: Same regex and length constraints as name. Must be a single directory segment (no slashes).
- **Content size**: Max 100,000 characters per SKILL.md (~36k tokens at 2.75 chars/token). Max 1 MiB (1,048,576 bytes) per supporting file.
- **Frontmatter**: Must start with `---`, must close with `---`, must parse as valid YAML mapping, must contain `name` and `description` fields. Description capped at 1,024 characters. Body after frontmatter must be non-empty.
- **File paths**: Supporting files must be under one of four allowed subdirectories (`references`, `templates`, `scripts`, `assets`). Path traversal (`..`) is rejected. Must include a filename, not just a directory.
- **Name collisions**: Checked across all directories via `_find_skill()` which recursively searches `~/.hermes/skills/` for matching directory names.

### 2.4 Atomic Write Pattern

All file writes use an atomic write pattern (`_atomic_write_text`, lines 243-272):

```python
fd, temp_path = tempfile.mkstemp(
    dir=str(file_path.parent),
    prefix=f".{file_path.name}.tmp.",
)
try:
    with os.fdopen(fd, "w", encoding=encoding) as f:
        f.write(content)
    os.replace(temp_path, file_path)
except Exception:
    os.unlink(temp_path)
    raise
```

The temp file is created in the same directory as the target (ensuring same filesystem for `os.replace` atomicity), then atomically swapped into place. On failure, the temp file is cleaned up and the original is untouched.

### 2.5 Fuzzy Patching

The `patch` action delegates to `tools/fuzzy_match.py` (482 lines) for find-and-replace operations. This handles whitespace normalization, indentation differences, escape sequences, and block-anchor matching -- preventing the agent from failing on minor formatting mismatches when patching its own skills.

### 2.6 Security Scanning on Write

Every create, edit, patch, and write_file operation runs the full security scanner (`_security_scan_skill`) after writing. If the scan blocks the skill, the operation is rolled back to the original content. For creates, the entire skill directory is deleted. For edits and patches, the original content is restored via atomic write. This means agent-created skills are subject to the same security scrutiny as community hub installs.

### 2.7 Cache Invalidation

After any successful skill mutation, `clear_skills_system_prompt_cache(clear_snapshot=True)` is called (`skill_manager_tool.py`, lines 621-625) to invalidate both the in-process LRU cache and the disk snapshot, ensuring the next system prompt rebuild picks up the changes.

## 3. Self-Improvement Loop

### 3.1 Nudge System

The self-improvement mechanism is driven by two interval-based counters in `run_agent.py`:

**Memory nudge** (turn-based):
- Counter: `_turns_since_memory`, incremented per user turn (line 6470)
- Default interval: 10 turns (`_memory_nudge_interval`, line 1052)
- Configurable via `config.yaml` at `memory.nudge_interval`
- Resets to 0 when the agent uses the `memory` tool (line 5673)

**Skill nudge** (iteration-based):
- Counter: `_iters_since_skill`, incremented per tool-calling iteration within a turn (line 6699)
- Default interval: 10 iterations (`_skill_nudge_interval`, line 1144)
- Configurable via `config.yaml` at `skills.creation_nudge_interval`
- Resets to 0 when the agent uses the `skill_manage` tool (line 5676)

The memory nudge is checked at the start of `run_conversation` (line 6467-6472). The skill nudge is checked after the agent loop completes (lines 8456-8461). Both triggers are evaluated independently.

### 3.2 Background Review Agent

When either nudge fires, a background review is spawned (lines 8463-8473):

```python
if final_response and not interrupted and (_should_review_memory or _should_review_skills):
    try:
        self._spawn_background_review(
            messages_snapshot=list(messages),
            review_memory=_should_review_memory,
            review_skills=_should_review_skills,
        )
    except Exception:
        pass  # Background review is best-effort
```

The `_spawn_background_review` method (lines 1588-1693) creates a **forked AIAgent instance** running in a **daemon thread**:

- The forked agent receives the same model, platform, and provider configuration
- It shares the same `_memory_store` instance (direct reference, not a copy)
- Its own nudge intervals are set to 0 to prevent recursive reviews
- Max iterations capped at 8 to limit cost
- `quiet_mode=True`, stdout/stderr redirected to devnull
- All output is suppressed during execution; only a summary is surfaced afterward

After the review agent completes, the thread scans its messages for successful tool calls (creates, updates, additions) and prints a compact summary to the user (e.g., "Memory updated" or "Skill 'foo' created").

### 3.3 Review Prompts

Three review prompts are defined (exact text from `run_agent.py`, lines 1553-1586):

**Memory-only review** (`_MEMORY_REVIEW_PROMPT`):

> Review the conversation above and consider saving to memory if appropriate.
>
> Focus on:
> 1. Has the user revealed things about themselves -- their persona, desires, preferences, or personal details worth remembering?
> 2. Has the user expressed expectations about how you should behave, their work style, or ways they want you to operate?
>
> If something stands out, save it using the memory tool. If nothing is worth saving, just say 'Nothing to save.' and stop.

**Skill-only review** (`_SKILL_REVIEW_PROMPT`):

> Review the conversation above and consider saving or updating a skill if appropriate.
>
> Focus on: was a non-trivial approach used to complete a task that required trial and error, or changing course due to experiential findings along the way, or did the user expect or desire a different method or outcome?
>
> If a relevant skill already exists, update it with what you learned. Otherwise, create a new skill if the approach is reusable.
> If nothing is worth saving, just say 'Nothing to save.' and stop.

**Combined review** (`_COMBINED_REVIEW_PROMPT`):

> Review the conversation above and consider two things:
>
> **Memory**: Has the user revealed things about themselves -- their persona, desires, preferences, or personal details? Has the user expressed expectations about how you should behave, their work style, or ways they want you to operate? If so, save using the memory tool.
>
> **Skills**: Was a non-trivial approach used to complete a task that required trial and error, or changing course due to experiential findings along the way, or did the user expect or desire a different method or outcome? If a relevant skill already exists, update it. Otherwise, create a new one if the approach is reusable.
>
> Only act if there's something genuinely worth saving. If nothing stands out, just say 'Nothing to save.' and stop.

The combined prompt is used when both memory and skill nudges fire simultaneously.

### 3.4 Skill Injection into System Prompts

Skills are injected into the system prompt via `agent/prompt_builder.py` using a **two-layer cache**:

**Layer 1: In-process LRU cache** (lines 270-273, 462-472)
- `OrderedDict` keyed by `(skills_dir, external_dirs, available_tools, available_toolsets)`
- Max 8 entries (`_SKILLS_PROMPT_CACHE_MAX`)
- Protected by a `threading.Lock`
- Checked first on every system prompt build

**Layer 2: Disk snapshot** (lines 276-338)
- Stored at `~/.hermes/.skills_prompt_snapshot.json`
- Contains a manifest of mtime/size pairs for all SKILL.md and DESCRIPTION.md files
- Validated by comparing the manifest against current filesystem state
- Survives process restarts (cold-start optimization)
- Written atomically via `atomic_json_write`

**Cold path** (lines 508-548): When both cache layers miss, a full filesystem scan reads all SKILL.md files, parses frontmatter, checks platform compatibility, evaluates conditional activation rules, and builds the skill index. The result is persisted to disk for future cold starts.

The injected prompt includes a mandatory instruction block (lines 622-636):

> Before replying, scan the skills below. If one clearly matches your task, load it with skill_view(name) and follow its instructions. If a skill has issues, fix it with skill_manage(action='patch'). After difficult/iterative tasks, offer to save as a skill.

Skills appear as a categorized index under `<available_skills>` tags with name and truncated description (max 60 chars).

## 4. Security Model

### 4.1 Overview

The security scanner (`tools/skills_guard.py`, 1,106 lines) provides regex-based static analysis, structural checks, invisible unicode detection, and an optional LLM audit layer. Every skill -- whether downloaded from a hub, created by the agent, or installed from a community source -- passes through this scanner.

### 4.2 Trust Levels and Install Policy

Four trust levels with a policy matrix mapping trust level + scan verdict to an install decision (from `skills_guard.py`, lines 38-48):

| Trust Level | Safe | Caution | Dangerous |
|-------------|------|---------|-----------|
| `builtin` | allow | allow | allow |
| `trusted` | allow | allow | block |
| `community` | allow | block | block |
| `agent-created` | allow | allow | ask |

Trust level resolution (`_resolve_trust_level`, lines 1057-1081):
- `agent-created`: Skills created by the agent itself
- `builtin`: Official optional skills shipped with the repo (source starts with `official/`)
- `trusted`: Only `openai/skills` and `anthropics/skills` repos
- `community`: Everything else (default)

The `should_allow_install` function returns a three-valued result: `True` (allow), `False` (block), or `None` (needs user confirmation, used for `ask` policy).

### 4.3 Regex Threat Patterns

The scanner contains **122 regex patterns** across 14 threat categories:

| Category | Count | Severity Breakdown |
|----------|-------|--------------------|
| exfiltration | 24 | 11 critical, 13 high |
| injection | 19 | 9 critical, 8 high, 2 medium |
| obfuscation | 14 | 1 critical, 7 high, 5 medium, 1 low |
| persistence | 12 | 4 critical, 1 high, 7 medium |
| supply_chain | 10 | 3 critical, 7 medium |
| network | 9 | 3 critical, 4 high, 2 medium |
| destructive | 8 | 6 critical, 1 high, 1 medium |
| credential_exposure | 6 | 6 critical |
| execution | 6 | 4 high, 2 medium |
| privilege_escalation | 5 | 3 critical, 2 high |
| traversal | 5 | 1 critical, 2 high, 2 medium |
| mining | 2 | 1 critical, 1 medium |

Notable pattern examples per category:
- **Exfiltration**: `curl`/`wget`/`fetch`/`requests` with secret env vars, SSH/AWS/GPG/Kubernetes credential directory access, DNS exfiltration, markdown image-based exfiltration, environment variable dumping
- **Injection**: Prompt injection ("ignore previous instructions"), role hijacking, DAN/developer mode jailbreaks, HTML comment injection, hidden divs, fake policy/update announcements
- **Obfuscation**: base64 decode-and-pipe, eval/exec with string args, echo piped to interpreter, `chr()` building, Python `__import__('os')`, codecs decode
- **Persistence**: crontab, shell RC files, SSH authorized_keys, systemd services, launchd agents, sudoers modification, global git config, agent config files (AGENTS.md, CLAUDE.md, .cursorrules, .hermes/config.yaml)
- **Supply chain**: curl-pipe-to-shell, unpinned pip/npm install, PEP 723 inline deps, uv run, remote resource fetching, git clone, docker pull
- **Credential exposure**: Hardcoded API keys, embedded private keys, GitHub PATs, OpenAI keys, Anthropic keys, AWS access key IDs

### 4.4 Structural Checks

The `_check_structure` function (`skills_guard.py`, lines 734-848) enforces:

- **File count limit**: Max 50 files per skill
- **Total size limit**: Max 1,024 KB (1 MB) total
- **Single file size limit**: Max 256 KB per individual file
- **Symlink escape detection**: Symlinks must resolve within the skill directory; external symlinks are flagged as critical
- **Binary file detection**: Suspicious binary extensions (`.exe`, `.dll`, `.so`, `.dylib`, `.bin`, `.dat`, `.com`, `.msi`, `.dmg`, `.app`, `.deb`, `.rpm`) are flagged as critical
- **Unexpected executables**: Files with executable permission that are not recognized script types (`.sh`, `.bash`, `.py`, `.rb`, `.pl`) are flagged as medium

### 4.5 Invisible Unicode Detection

The scanner checks for 17 invisible/zero-width unicode characters (`skills_guard.py`, lines 505-523):

Zero-width characters (U+200B space, U+200C non-joiner, U+200D joiner, U+2060 word joiner, U+2062-U+2064 invisible math operators, U+FEFF BOM), directional formatting characters (U+202A-U+202E LTR/RTL embedding/override, U+2066-U+2069 LTR/RTL isolate). Each finding is flagged as high severity in the injection category.

### 4.6 LLM Audit Layer

An optional LLM-based security audit (`llm_audit_skill`, lines 897-1000) provides a second pass after static analysis:

- Skipped if static scan already returns "dangerous" (no point in re-confirming)
- Collects all text content from the skill (capped at 15,000 chars)
- Sends to the user's configured model via `agent/auxiliary_client.py`
- LLM can only **raise** severity, never lower it
- Findings from the LLM are merged into the static result
- Best-effort: failures do not block installation

### 4.7 Context File Scanning

The prompt builder (`agent/prompt_builder.py`, lines 36-73) also scans context files (AGENTS.md, .cursorrules, SOUL.md, HERMES.md) for prompt injection before injecting them into system prompts. This uses a smaller subset of 10 threat patterns plus a set of invisible unicode characters.

## 5. Skill Hub and Distribution

### 5.1 Source Adapters

The skills hub (`tools/skills_hub.py`, 2,707 lines) implements a multi-source adapter pattern:

- **OptionalSkillSource**: Official optional skills shipped with the repo in `optional-skills/` (categories: autonomous-ai-agents, blockchain, communication, creative, devops, email, health, mcp, migration, mlops, productivity, security)
- **GitHubSource**: Fetch skills from any GitHub repo via the Contents API
- **ClawHub**: Community skill marketplace at clawhub.ai
- **Claude Marketplace**: Skills from claude-marketplace
- **LobHub**: Skills from lobehub
- **Taps**: User-configured custom GitHub repo sources (managed via `TapsManager`)

Each source implements a `SkillSource` ABC with `search()`, `inspect()`, and `fetch()` methods, returning `SkillMeta` and `SkillBundle` dataclasses.

### 5.2 Installation Flow

The installation flow (`hermes_cli/skills_hub.py`, `do_install`, lines 307-446) follows a quarantine-scan-confirm-install pipeline:

1. **Resolve**: If the identifier is a short name (no slashes), resolve via unified search across all sources
2. **Fetch**: Download the skill bundle from the matched source adapter
3. **Quarantine**: Write the bundle to `~/.hermes/skills/.hub/quarantine/<skill-name>/` (from `quarantine_bundle`, `tools/skills_hub.py`, lines 2480-2502). Path traversal is blocked via `_validate_bundle_rel_path`.
4. **Scan**: Run the full security scanner on the quarantined files
5. **Policy check**: Evaluate `should_allow_install` against trust level + verdict
6. **Confirm**: Show the user a disclaimer panel (different for official vs. third-party). Skip confirmation for slash command installs (implicit consent) or `--force` flag.
7. **Install**: Move from quarantine to `~/.hermes/skills/[category/]<name>/` via `install_from_quarantine`
8. **Record**: Write provenance to lock file, append to audit log
9. **Invalidate cache**: Clear skills prompt cache so the new skill appears immediately

### 5.3 Lock File for Provenance

The `HubLockFile` class (`tools/skills_hub.py`, lines 2332-2395) manages `~/.hermes/skills/.hub/lock.json` with a versioned schema:

```json
{
  "version": 1,
  "installed": {
    "skill-name": {
      "source": "github",
      "identifier": "owner/repo/skill-name",
      "trust_level": "community",
      "scan_verdict": "safe",
      "content_hash": "sha256:abc123...",
      "install_path": "category/skill-name",
      "files": ["SKILL.md", "references/guide.md"],
      "metadata": {},
      "installed_at": "2026-01-15T10:30:00+00:00",
      "updated_at": "2026-01-15T10:30:00+00:00"
    }
  }
}
```

Content hashes use SHA-256 (truncated to 16 hex chars) computed over all files in the skill directory. The lock file tracks source, identifier, trust level, scan verdict, file list, and timestamps for every hub-installed skill.

### 5.4 Additional Hub Features

- **Update checking**: `check_for_skill_updates` compares installed skills against upstream
- **Audit**: Re-run security scans on installed hub skills
- **Publish**: Create a GitHub PR to contribute a skill back to a repo (fork, branch, upload, PR)
- **Snapshot export/import**: Portable JSON format for replicating skill configurations across machines
- **Taps**: User-managed custom GitHub repo sources via `taps.json`
- **Audit log**: Append-only log at `~/.hermes/skills/.hub/audit.log` recording all install/block events

## 6. Strengths

**Security-first design.** The security scanner is thorough and well-categorized. 122 regex patterns across 14 categories cover a wide attack surface. The trust-level policy matrix is clean and extensible. The quarantine-then-scan flow prevents untrusted code from ever reaching the skills directory without inspection.

**Atomic writes everywhere.** The `tempfile.mkstemp` + `os.replace` pattern prevents partial writes from corrupting skill files. Rollback on security scan failure is implemented consistently across create, edit, patch, and write_file operations.

**Fuzzy patching.** Delegating to a dedicated fuzzy match engine for patch operations is pragmatic -- it accounts for the reality that LLMs produce slightly imprecise text matches and prevents unnecessary patch failures.

**Two-layer caching.** The in-process LRU + disk snapshot approach is well-engineered. The cold-start manifest validation (mtime + size comparison) avoids stale cache bugs without expensive content hashing on every startup.

**Conditional activation.** The `fallback_for_toolsets`/`requires_toolsets` mechanism allows skills to auto-hide when they are not relevant (e.g., a "manual email" skill hides when the email toolset is available), keeping the skill index lean and context-efficient.

**Agent-as-first-class-editor.** The skill manager gives the agent the same create/edit/patch/delete/write_file/remove_file surface area that a human developer would want. The schema descriptions include clear heuristics for when to create vs. update.

**Hub infrastructure.** The quarantine/scan/confirm/install pipeline, lock file provenance tracking, taps system, snapshot export/import, and publish-via-PR workflow form a complete distribution system.

## 7. Gaps and Weaknesses

### 7.1 No Quality Metrics

There is no tracking of skill usage frequency, success rates, or user ratings. The system cannot distinguish a skill used 100 times from one never used. There is no mechanism to surface "most useful" skills or deprecate low-value ones based on data. The only quality signal is whether the agent's LLM review decides something is "worth saving."

### 7.2 No Lifecycle Management

Skills have no lifecycle states (draft, stable, deprecated). There is no version history or rollback capability. A bad `edit` action replaces the SKILL.md content entirely with no way to recover the previous version (the atomic write ensures the file is not corrupted, but the old content is gone). There is no concept of skill versioning aligned to the agent or project version.

### 7.3 No Data-Driven Improvement

The background review is purely LLM-judgment-based. The review prompts ask the LLM to introspect on whether "a non-trivial approach was used" or whether "the user expected a different method." There is no telemetry feeding back into this decision -- no tracking of which tasks succeeded, which skills were loaded but abandoned, or which skills led to user corrections.

### 7.4 No Deduplication Detection

The `_find_skill` function checks for exact name collisions but not semantic overlap. The agent could create `deploy-kubernetes`, `k8s-deployment`, and `kubernetes-deploy` as three separate skills covering the same procedure. There is no similarity detection, consolidation mechanism, or duplicate warning.

### 7.5 Fire-and-Forget Error Handling in Background Review

The background review thread catches all exceptions with a bare `logger.debug` (line 1675):

```python
except Exception as e:
    logger.debug("Background memory/skill review failed: %s", e)
```

This means review failures are silently swallowed. If the review agent consistently fails (model errors, rate limits, OOM), the system has no mechanism to detect, report, or adapt. The outer spawn also catches exceptions with `pass` (line 8473).

### 7.6 No User Feedback Integration

The user cannot rate, approve, or reject a skill the agent created. The background review creates skills without asking for confirmation (the review agent runs in quiet mode with stdout redirected to devnull). The user sees only a one-line summary like "Skill 'foo' created" after the fact. There is no "thumbs up/down" or correction mechanism that feeds back into skill quality.

### 7.7 Monolithic File Architecture

Key files are extremely large:
- `run_agent.py`: 8,723 lines
- `cli.py`: 7,954 lines
- `tools/skills_hub.py`: 2,707 lines

The background review logic, nudge counters, and skill-related prompt injection are scattered across `run_agent.py` at lines 1040-1150, 1549-1693, 5672-5676, 6456-6473, 6695-6699, and 8455-8473. This makes the self-improvement system difficult to reason about, test in isolation, or extend.

### 7.8 Shared Mutable State in Background Review

The background review agent receives a direct reference to `self._memory_store` (line 1626), not a copy. This means the background thread and the main conversation thread can race on memory writes. There is no locking or coordination mechanism visible in the review spawn code.

### 7.9 No Skill Testing or Validation

There is no mechanism to test whether a skill's instructions actually work. Skills contain shell commands, code snippets, and procedural steps, but there is no sandbox execution, dry-run mode, or automated validation that the steps produce the expected outcome.

## 8. Extractable Patterns

### 8.1 Atomic Write Pattern

**Source**: `tools/skill_manager_tool.py`, lines 243-272

Worth porting directly. The `tempfile.mkstemp` in the same directory + `os.replace` pattern is the correct way to do crash-safe file writes on POSIX and Windows. The cleanup-on-failure path is also correct.

### 8.2 Fuzzy Patching Mechanism

**Source**: `tools/fuzzy_match.py` (482 lines)

The idea of delegating LLM-generated patches to a fuzzy matcher that handles whitespace normalization, indentation differences, and escape sequences is valuable. This reduces agent patch failure rates significantly.

### 8.3 Trust-Level Policy Matrix

**Source**: `tools/skills_guard.py`, lines 38-48

The matrix-based approach (trust level x verdict -> decision) with a three-valued result (allow/block/ask) is clean and extensible. Adding a new trust level or adjusting policy for a source is a one-line change.

### 8.4 Two-Layer Caching (In-Process LRU + Disk Snapshot)

**Source**: `agent/prompt_builder.py`, lines 270-338, 436-645

The cache key design (directory paths + available tools/toolsets), manifest-based disk validation (mtime + size, not content hash), and atomic JSON writes for the snapshot are all well-considered. The cold-start optimization is particularly valuable for CLI agents that restart frequently.

### 8.5 Security Regex Pattern Library

**Source**: `tools/skills_guard.py`, lines 82-484

The categorized pattern library (122 patterns, 14 categories) is a significant investment in security coverage. The patterns are well-documented with pattern IDs, severity levels, category tags, and human-readable descriptions. Categories worth extracting:

- **Exfiltration patterns** (24): Covers shell commands, Python/Node/Ruby API calls, DNS exfil, markdown-based exfil
- **Injection patterns** (19): Covers prompt injection, jailbreaks, social engineering, hidden instructions
- **Persistence patterns** (12): Covers cron, shell RC files, SSH keys, systemd, launchd, agent config files
- **Supply chain patterns** (10): Covers curl-pipe-to-shell, unpinned installs, remote resource fetching

### 8.6 Conditional Activation Rules

**Source**: `agent/skill_utils.py`, lines 231-245 and `agent/prompt_builder.py`, lines 405-433

The `fallback_for_toolsets`/`requires_toolsets` pattern for context-dependent skill visibility is elegant. It prevents skill index bloat and keeps prompt tokens focused on relevant skills.

### 8.7 Quarantine-Scan-Install Pipeline

**Source**: `tools/skills_hub.py`, `hermes_cli/skills_hub.py`

The full pipeline (fetch -> quarantine to isolated directory -> security scan -> policy check -> user confirmation -> move to install location -> record provenance -> invalidate cache) is a solid template for any system that installs untrusted agent-generated or community-contributed content.

### 8.8 Lock File Provenance Tracking

**Source**: `tools/skills_hub.py`, lines 2332-2395

Tracking source, identifier, trust level, scan verdict, content hash, file list, and timestamps for every installed skill provides an audit trail that supports update checking, security re-auditing, and snapshot export/import.

---

**Repository**: https://github.com/NousResearch/hermes-agent
**Clone location**: `~/workspace/tmp/NousResearch-hermes-agent`
**Analysis date**: 2026-04-01
**Key files referenced**:
- `tools/skill_manager_tool.py` (743 lines) -- Skill CRUD operations
- `tools/skills_guard.py` (1,106 lines) -- Security scanning
- `tools/skills_hub.py` (2,707 lines) -- Hub source adapters and state management
- `tools/fuzzy_match.py` (482 lines) -- Fuzzy find-and-replace engine
- `agent/prompt_builder.py` (817 lines) -- System prompt assembly with skill index
- `agent/skill_utils.py` (277 lines) -- Shared skill metadata utilities
- `hermes_cli/skills_hub.py` (1,220 lines) -- CLI/slash command interface for skills hub
- `run_agent.py` (8,723 lines) -- Agent core, background review, nudge system
