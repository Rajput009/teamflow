# TeamFlow — Project Overview

## What TeamFlow Is

TeamFlow is a **backend for an organization's internal project-management platform** — a simplified combination of Trello (projects & tasks) + Slack-style notifications + company user management.

We build the **backend/API only**. There is no frontend.

> A web-based project and team management platform where organizations can manage
> employees, projects, tasks, deadlines, permissions, and notifications.

## Why This Project Exists

The goal is not "project management apps are cool." The goal is that this one
business domain forces us to solve the problems a professional Python backend
developer actually faces:

| Real problem | Where it shows up in TeamFlow |
|---|---|
| Authentication | Register / login / JWT / refresh tokens |
| Authorization (RBAC) | OWNER vs ADMIN vs MANAGER vs MEMBER permissions |
| Multi-tenancy | Company A must never see Company B's data |
| Data modeling | Users ↔ Orgs ↔ Projects ↔ Tasks relationships |
| API design | Filtering, pagination, sorting, consistent errors |
| Async processing | Notifications via Redis + Celery background jobs |
| Testing | Pytest suite covering business rules and security rules |
| Deployment | Docker Compose, CI, logging |

**The code is evidence. The concepts are the portfolio.**

## Core Domain

```text
Organization (a company)
├── Members (users with a role: OWNER / ADMIN / MANAGER / MEMBER)
├── Projects
│   ├── Project members (who can see this project)
│   └── Tasks
│       ├── Assignment (one user)
│       ├── Comments
│       └── Activity history (audit trail)
└── Activity log (org-wide)
```

## Goals

1. Every endpoint enforces authentication, tenancy isolation, and role permissions.
2. Every feature is documented *before* coding (see `features/_template.md`).
3. Every feature has tests derived from its documented failure modes.
4. `docker compose up` starts the whole system.
5. The repo reads like a professional codebase: layered architecture, migrations, CI config.

## Non-Goals

- No frontend/UI.
- No real-time websocket updates before V5 (polling/notifications first).
- No billing/payments integration (billing is simulated: only the permission exists).
- No file uploads before V4+.
- Not aiming for Trello-level features (drag-drop boards, checklists) — depth of
  engineering beats breadth of features.

## Version Roadmap

Each version adds a layer of engineering maturity. Full build order: `09-roadmap.md`.

### V1 — Basic backend

Register, login, JWT access + refresh tokens, organizations, projects, tasks,
assignment. Learn: FastAPI, async SQLAlchemy, Alembic, JWT, validation.

### V2 — Real application

Roles enforced everywhere, project members, comments, filtering/pagination/search,
activity log. Learn: RBAC, relationships, API design, business logic.

### V3 — Production features

Redis, Celery workers, email notifications, in-app notifications, caching basics.
Learn: queues, workers, asynchronous architecture.

### V4 — Engineering

Pytest coverage to target, Docker Compose full stack, GitHub Actions CI, structured
logging, global error handling, monitoring hooks. Learn: testing, CI/CD, observability.

### V5 — Scale

Rate limiting, DB indexes/query optimization, load testing, idempotency keys,
concurrency handling (optimistic locking on task updates). Learn: performance,
reliability under load.

## Success Criteria

A stranger reading this repository should be able to see:

- [ ] Clean layered architecture (router → service → repository)
- [ ] Complete auth flow incl. revocable refresh tokens
- [ ] Enforced multi-tenancy with tests proving isolation
- [ ] RBAC permission matrix implemented and tested
- [ ] Background job pipeline (V3+)
- [ ] Passing test suite with meaningful coverage (V4)
- [ ] One-command local setup (`docker compose up`)
