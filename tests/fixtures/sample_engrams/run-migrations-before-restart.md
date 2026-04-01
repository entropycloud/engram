---
name: "run-migrations-before-restart"
version: 3
description: "Run Alembic migrations before restarting the app service"
state: candidate
created: "2026-03-15T10:30:00Z"
updated: "2026-03-28T14:20:00Z"
supersedes: ~
superseded_by: ~
triggers:
  tags: [deploy, alembic, migration, systemctl]
  patterns:
    - "deploy|restart.*service|systemctl.*restart"
    - "alembic|migration"
  projects:
    - "/data/dev/myapp/*"
  files:
    - "**/alembic.ini"
    - "**/alembic/versions/*.py"
trust: agent-created
allowed_tools:
  - Bash
  - Read
  - Edit
restricted_tools: []
metrics:
  usage_count: 12
  success_count: 10
  override_count: 1
  last_used: "2026-03-28T14:20:00Z"
  last_evaluated: "2026-03-28T14:20:00Z"
  quality_score: 0.82
  streak: 3
lineage:
  parent: ~
  created_from: "session-2026-03-15-abc123"
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
