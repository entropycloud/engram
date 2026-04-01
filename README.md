# Engram

Self-improving procedural memory for Claude Code.

Engram is a system that lets Claude Code learn from experience. Where Claude Code's existing memory stores declarative facts ("this project uses pytest"), engrams store procedural knowledge ("when deploying this project, always run migrations before restarting the service because the startup healthcheck queries new tables").

Engrams are created automatically when the agent discovers non-trivial approaches, evaluated based on real outcomes, and improved or retired based on quality signals over time.

## Status

Under construction. See [Architecture](projectDocs/designs/engram-architecture.md) and [Implementation Plan](projectDocs/plans/active/engram-implementation.md).

## License

MIT
