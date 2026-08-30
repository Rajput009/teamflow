---
id: WF-5
title: Docker finale — compose topology, broker flip, beat, secrets
type: research
labels: [wayfinder:research]
assignee: opencode
status: closed
blocking: []
blocked_by: [WF-4]
asset: null
---

## Question

What is the production **Docker** topology for TeamFlow, and how do we flip from
eager to a real broker safely?

Sub-questions (research first; some may become a Task checklist, since **Docker is
not yet installed** in this environment):

1. **Compose services** — `api`, `postgres`, `redis`, `worker`, `beat`, `nginx`:
   networks, volumes, healthchecks, restart policies. What's the canonical
   FastAPI + Celery + Redis + Postgres + nginx layout?
2. **Broker flip** — how to set `task_always_eager=False` and point Celery at Redis
   without breaking eager-mode tests? Env-driven toggle.
3. **Beat jobs** — deadline reminders, refresh-token cleanup: how registered and
   scheduled in compose.
4. **Secrets/env** — `.env` handling across containers; `LLM_API_KEY` and DB creds
   without leaking into images.
5. **nginx** — TLS termination / reverse proxy in front of the API.
6. **CI** — GitHub Actions running the mocked test suite + ruff on every push.

Outcome: a topology decision + a provisioning checklist (including "install Docker")
the hand-off effort executes. Research findings captured under
`docs/wayfinder/research/docker-topology.md`.

## Resolution

Decided **Docker topology + broker flip** for the finale.

- **Services:** one `Dockerfile` (`python:3.12-slim`) shared by `api`, `worker`, `beat`;
  plus `postgres:17`, `redis:7`, optional `nginx`. `depends_on` with
  `condition: service_healthy` (postgres `pg_isready`, redis `redis-cli ping`). One
  bridge network; only `api`/`nginx` ports published (postgres/redis internal).
- **nginx:** include a minimal reverse proxy in front of `api:8000`; **TLS deferred**.
- **Broker:** **Redis** (already planned) as both broker (`/0`) and result backend (`/1`);
  no RabbitMQ.
- **Alembic:** run `alembic upgrade head` in the `api` entrypoint (idempotent); `worker`/
  `beat` start after `api` is healthy. Single obvious migration point, no race.
- **Beat:** register `deadline-reminders` (hourly → enqueues notification tasks) and
  `token-cleanup` (daily expired refresh-token deletion) in `app/workers/celery_app.py`
  `beat_schedule`; single beat instance.
- **Broker flip:** env-driven — production sets `TASK_ALWAYS_EAGER=False` +
  `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` to Redis; **tests stay
  `task_always_eager=True` with no broker**, so CI never touches the network. Transparent
  to endpoints (per WF-4).
- **Secrets:** `.env` via `env_file`; `LLM_API_KEY`, `DATABASE_URL`, `REDIS_URL`,
  `SECRET_KEY` never baked into the image.
- **CI:** GitHub Actions on push — checkout, Python setup, install, `ruff check`, mocked
  `pytest`; optional `docker compose build` job.
- **Compose layout:** single `docker-compose.yml` + `.env`; a dev/prod split is
  over-engineering for this project.
