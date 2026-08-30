# Research: Docker topology for TeamFlow (FastAPI + Celery + Redis + Postgres + nginx)

Decision-support document for Wayfinder ticket **WF-5**. Researched inline (the
research-subagent mechanism was unavailable in this environment); sources cited
inline. No application code or Dockerfiles are written here — this informs the
hand-off effort.

## 1. Compose services

Canonical layout (confirmed by multiple FastAPI+Celery tutorials:
[oneuptime](https://oneuptime.com/blog/post/2026-02-08-how-to-set-up-a-fastapi-postgresql-celery-stack-with-docker-compose/view),
[testdriven.io](https://testdriven.io/courses/fastapi-celery/docker)):

- **`api`** — FastAPI/uvicorn, same image as workers, command `uvicorn app.main:app`.
- **`postgres`** (17) — `postgres:17-alpine`, volume for data, healthcheck
  `pg_isready -U <user> -d <db>`.
- **`redis`** — `redis:7-alpine`, healthcheck `redis-cli ping`.
- **`worker`** — same image, command `celery -A app.workers.celery_app worker`.
- **`beat`** — same image, command `celery -A app.workers.celery_app beat`.
- **`nginx`** (optional) — reverse proxy to `api:8000`.

All app services share **one `Dockerfile`** (`python:3.12-slim`), differing only by
command. `depends_on` uses `condition: service_healthy` so api/worker/beat wait for
postgres + redis. A single `app-network` bridge; postgres/redis not exposed publicly
(only api/nginx ports published).

## 2. Alembic in containers

The well-established pattern ([testdriven.io](https://testdriven.io/courses/fastapi-celery/docker))
is an **entrypoint script** that waits for Postgres, runs `alembic upgrade head`, then
`exec`s the service command. `alembic upgrade head` is **idempotent** (no-op if already
at head), so running it in `api`, `worker`, and `beat` entrypoints is safe — but to
avoid contention, the common simplification is to run it in the `api` start script only
and have workers depend on `api` (or just accept the harmless idempotent re-run). For
TeamFlow, recommend: **run `alembic upgrade head` in the `api` entrypoint**; workers
start after `api` is healthy. Alternatively a tiny `migrate` init container. Either is
fine; pick one in the decision round.

## 3. Broker flip (`task_always_eager`)

Celery uses the lowercase setting `task_always_eager`
([Celery docs](https://docs.celeryq.dev/en/latest/userguide/configuration.html));
default is **False** (so production-safe by default). TeamFlow currently sets it `True`
in dev. The flip is purely env-driven: in production compose set
`TASK_ALWAYS_EAGER=False` (mapped into `Settings`) and point Celery at Redis via
`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` (redis://redis:6379/0 and /1).
The **test suite stays on `task_always_eager=True`** and never sets a real broker URL,
so CI never touches Redis — exactly the current green-path behaviour.

Crucially, this flip is **transparent to endpoints** (per WF-4): sync endpoints `await`
inline; only V5 agent runs are enqueued. No endpoint code changes.

## 4. Beat jobs

`celery beat` reads `celery_app.conf.beat_schedule`. Register the two periodic tasks:
- **deadline reminders** — scans tasks due soon, enqueues notification tasks.
- **refresh-token cleanup** — deletes expired refresh tokens.
Beat service just needs the same app image + broker env. Periodic task definitions
live in `app/workers/celery_app.py` (or a `beat_schedule` dict). Single beat instance
(no duplication) — fine for a portfolio deployment.

## 5. Secrets / env

Use `env_file: .env` per service (compose supports it). Keep `LLM_API_KEY`,
`DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` in `.env` — **never baked into the image**
(images are built from source, no secrets). Pass `DATABASE_URL`, `CELERY_BROKER_URL`,
`CELERY_RESULT_BACKEND`, `LLM_API_KEY` to `api`/`worker`/`beat`. `.env` is gitignored.
Note: our app reads `redis_url` from `Settings`, so set `REDIS_URL=redis://redis:6379/0`.

## 6. nginx

A minimal reverse proxy in front of `api:8000` is standard
([testdriven.io prod variant](https://github.com/Madi-S/fastapi-celery-template)). For a
portfolio demo, **TLS can be deferred** (run nginx as plain HTTP proxy, or skip nginx
entirely and publish `api:8000`). Recommend: include nginx as a simple proxy for
completeness, TLS optional/deferred. This is a decision item.

## 7. CI (GitHub Actions)

Workflow on push: checkout → setup Python → `uv pip install` → `ruff check` →
`pytest` (mocked, `task_always_eager=True`, no Docker/network). Optionally a separate
`docker compose build` job (does not need to run tests). This matches TeamFlow's
existing constraint that the suite never touches the network.

## Summary for the decision round

Research confirms a low-risk, conventional topology. The open *decisions* (not facts)
are: (a) nginx now or deferred, (b) Alembic entrypoint strategy, (c) confirm Redis
broker (vs RabbitMQ — Redis is simpler and already planned), (d) beat schedule contents,
(e) CI shape. These are grilled in the WF-5 ticket.
