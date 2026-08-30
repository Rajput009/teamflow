# Architecture

## High-Level Topology (final state, V4+)

```text
                  INTERNET
                     │
                     ▼
                  NGINX            (reverse proxy, TLS termination)
                     │
                     ▼
               FASTAPI API  ──────►  PostgreSQL 16
                 │        \               ▲
                 │         \              │ async (asyncpg)
                 ▼          ▼             │
              Redis  ──► Celery Workers ─┘
             (broker)     (emails, notifications)
```

- **V1–V2:** FastAPI + PostgreSQL only. No Redis/Celery yet.
- **V3:** add Redis (broker + cache) and Celery workers.
- **V4:** add NGINX, full Docker Compose, CI.

## Layered Architecture

Every feature flows through the same layers. Each layer has exactly one job:

```text
HTTP Request
     ↓
Router        api/v1/*.py        Parse request, auth dependency, call service,
     ↓                           return response. NO business logic here.
Service       services/*.py      Business rules: permissions, tenancy checks,
     ↓                           orchestration. The heart of the app.
Repository    repositories/*.py  Database queries only. No business rules.
     ↓
Models        models/*.py        SQLAlchemy ORM tables.
     ↓
PostgreSQL

Schemas       schemas/*.py       Pydantic models: validation in/out at the router edge.
```

### Why not just put everything in the router?

A fat router works at first, then collapses under its own weight. Separating layers gives us:

- **Testability** — test services without HTTP; test routers with a fake service.
- **Reuse** — a Celery worker can call the same `task_service.assign()` as the router.
- **Clarity** — "where does permission checking live?" has one answer: the service layer.

## Request Lifecycle

```text
1. Request hits FastAPI route
2. Dependencies run: get_db (session), get_current_user (JWT), get_membership (RBAC context)
3. Pydantic validates body/query params → 422 on bad input
4. Router calls service function
5. Service checks permissions & tenancy → raises domain exceptions if violated
6. Repository executes queries
7. Service commits via session, returns result
8. Router maps result to response schema
9. Global exception handlers map any raised error to the error envelope (see 06)
10. Structured log line written with request ID (V4)
```

## Project Structure

```text
teamflow/
├── app/
│   ├── main.py                 App factory, middleware, exception handlers
│   ├── core/
│   │   ├── config.py           pydantic-settings: env-driven configuration
│   │   ├── security.py         Hashing, JWT encode/decode, token generation
│   │   └── exceptions.py       Domain exceptions + error envelope mapping
│   ├── db/
│   │   ├── base.py             Declarative base, naming conventions
│   │   └── session.py          Async engine + session factory
│   ├── models/                 SQLAlchemy models (one file per entity)
│   ├── schemas/                Pydantic schemas (one file per domain)
│   ├── repositories/           DB access per entity
│   ├── services/               Business logic per entity
│   ├── api/
│   │   ├── deps.py             Shared dependencies (get_db, get_current_user, ...)
│   │   └── v1/
│   │       ├── router.py       Aggregates all v1 routers
│   │       └── endpoints/      auth.py, organizations.py, projects.py, tasks.py, ...
│   ├── workers/                Celery app + tasks (V3)
│   └── tests/
│       ├── conftest.py         Event loop, test DB, client fixtures
│       ├── factories.py        Test data factories
│       ├── unit/               Service-level tests
│       └── integration/        Full-request tests through httpx AsyncClient
├── alembic/                    Migration environment + versions/
├── docs/                       This documentation
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml              Dependencies + tool config (ruff, pytest)
└── .github/workflows/ci.yml    (V4)
```

## Technology Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Modern typing, performance |
| Framework | FastAPI | Async-first, Pydantic integration, OpenAPI docs for free |
| Validation | Pydantic v2 | Request/response contracts, settings |
| Config | pydantic-settings | Type-safe env config, fail-fast on missing vars |
| ORM | SQLAlchemy 2.0 (**async**) | Industry standard, typed mappings |
| Driver | asyncpg | Fastest async PostgreSQL driver |
| Migrations | Alembic | Versioned, reviewable schema changes |
| Database | PostgreSQL 16 | Relational integrity, JSONB, enums, indexes |
| Auth | python-jose / PyJWT + passlib (argon2 or bcrypt) | JWT + password hashing |
| Queue (V3) | Celery + Redis | Standard background job stack |
| Testing (V4) | pytest, pytest-asyncio, httpx | Async-capable test stack |
| Lint/format | ruff | One tool, fast |
| Containers | Docker + Compose | Reproducible local setup |

## Configuration Management

All configuration comes from environment variables (12-factor), loaded by
`pydantic-settings` into a typed `Settings` object:

```python
class Settings(BaseSettings):
    database_url: PostgresDsn
    redis_url: RedisDsn          # V3
    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14
    environment: Literal["local", "staging", "production"]
```

Rules:
- No secrets in code or git. `.env` is gitignored; `.env.example` documents every var.
- Missing required config = crash at startup, never at first request.
