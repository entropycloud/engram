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
