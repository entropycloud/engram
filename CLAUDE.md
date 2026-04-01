# Engram

Self-improving procedural memory system for Claude Code.

## Project Structure

- `src/engram/` — Python package source
- `projectDocs/` — Project documentation (managed by doc-manager skill)
  - `designs/engram-architecture.md` — Architecture design (source of truth)
  - `plans/active/engram-implementation.md` — Phased implementation plan
  - `research/` — Prior art analysis (Hermes agent review)

## Development

- Python 3.11+, managed with `uv`
- Testing: `pytest` with TDD (write test first, then implement)
- Linting: `ruff`, Type checking: `mypy`
- Build: `hatchling`

## Conventions

- All engram data models use Pydantic v2
- File-based storage only (no databases)
- Atomic writes for all store operations (lock → tmp → fsync → rename)
- Security scanning on all engram content before write
- YAML pattern files for scanner (not hardcoded regex)
