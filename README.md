# TeamFlow

A backend for an organization's internal project-management platform —
a simplified combination of Trello-style projects/tasks + Slack-style notifications
+ company user management.

**Status:** V2 complete. FastAPI · PostgreSQL 16 · async SQLAlchemy 2.0 · Alembic · JWT · RBAC · 85 tests.

## What it does

Organizations register their company, invite employees under four roles
(OWNER / ADMIN / MANAGER / MEMBER), and manage projects and tasks with:

- **Multi-tenancy** — every query is scoped to your organization; Company B cannot
  read, modify, or even *detect* Company A's data (foreign resources return 404,
  indistinguishable from nonexistent).
- **RBAC** — a permission matrix enforced in the service layer: last-owner protection,
  admins-can't-touch-owners, members can only edit tasks assigned to them.
- **Project membership** — MEMBERs only see projects they belong to; MANAGER+ see all.
- **Authentication** — short-lived JWT access tokens + opaque refresh tokens stored
  hashed, with **rotation on use** and **reuse detection** (a replayed token revokes
  every token that user owns).
- **Comments** — task discussion threads with author-only deletion (ADMIN+ moderation).
- **Activity log** — an append-only audit trail written in the same transaction as
  each state change, with JSONB old/new value snapshots.
- **Filtering / pagination / search** on task lists; a consistent pagination envelope
  on every list endpoint.
- **Consistent error envelope** — every non-2xx response has `{code, message, details}`.

## Architecture

```text
HTTP Request
     ↓
Router        api/v1/endpoints/   parse + validate (Pydantic), no business logic
Service       services/           permissions, tenancy scoping, orchestration
Repository    repositories/       DB queries only — every one takes organization_id
Model         models/             SQLAlchemy 2.0 async ORM
     ↓
PostgreSQL
```

Key invariants (all enforced by tests):

1. No repository method fetches tenanted data without an `organization_id` scope.
2. Domain errors commit compensating writes before raising (e.g., refresh-token theft
   response); unexpected errors roll back everything.
3. Audit rows share the transaction of the change they describe.
4. Ordering/filter inputs are whitelisted — client input never becomes SQL identifiers.

Full specifications live in [`docs/`](docs/) — database schema, auth flows, RBAC matrix,
API contracts, testing strategy, and per-feature docs answering seven questions
(problem → data → endpoint → DB → failures → permissions → tests) *before* coding.

## Running locally

Requirements: Python 3.12+, PostgreSQL 16+.

```bash
# 1. Create two databases and a user
psql -U postgres -c "CREATE ROLE teamflow LOGIN PASSWORD 'teamflow';"
psql -U postgres -c "CREATE DATABASE teamflow OWNER teamflow;"
psql -U postgres -c "CREATE DATABASE teamflow_test OWNER teamflow;"

# 2. Configure
cp .env.example .env          # then set JWT_SECRET_KEY (see comment inside)

# 3. Install (or: python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]")
pip install -e ".[dev]"

# 4. Migrate both databases
alembic upgrade head
DATABASE_URL=postgresql+asyncpg://teamflow:teamflow@localhost:5432/teamflow_test alembic upgrade head

# 5. Run
uvicorn app.main:app --reload
# API docs at http://localhost:8000/docs
```

### Tests

```bash
DATABASE_URL=postgresql+asyncpg://teamflow:teamflow@localhost:5432/teamflow_test pytest
```

85 tests: auth lifecycle (incl. token rotation/replay attacks), multi-tenancy isolation,
the full RBAC permission matrix, comments, activity log, pagination math, plus unit tests
for hashing/JWT internals.

### Lint

```bash
ruff check app
```

## Roadmap

- [x] **V1** — auth, organizations, projects, tasks, assignment
- [x] **V2** — RBAC depth, project membership, comments, activity log, pagination
- [ ] **V3** — Redis + Celery background jobs (email/in-app notifications)
- [ ] **V4** — Docker Compose, CI, structured logging, coverage targets
- [ ] **V5** — rate limiting, query optimization, optimistic locking, load testing
