# Build Roadmap

One concept at a time. Each step: **learn the concept → code it → verify it**.
No step starts before the previous one runs.

## V1 — Basic Backend

| # | Step | Concepts learned | Done when |
|---|---|---|---|
| 1 | Project skeleton | pydantic-settings, app factory, health endpoint | `GET /health` returns 200; config fails fast on missing env |
| 2 | Database foundation | async engine, session dependency, Alembic | Migration creates empty schema; `/health/db` checks connection |
| 3 | Users & registration | Pydantic validation, password hashing, repositories | `POST /auth/register` → 201; duplicate email → 409 |
| 4 | Login & tokens | JWT signing, opaque refresh tokens, rotation | Login → token pair; refresh rotates; reuse triggers revocation |
| 5 | Protected routes | FastAPI dependencies, `get_current_user` | `GET /auth/me` works with token, 401 without |
| 6 | Organizations | Transactions (user + membership created atomically) | Creating org makes creator OWNER |
| 7 | Projects CRUD | Tenancy scoping in queries, service layer discipline | Org B cannot fetch Org A's project (404) — tested |
| 8 | Tasks + assignment | Relationships, enums, cross-entity validation | Assign to non-org-member → 422; happy path works |
| 9 | Error handling | Global exception handlers, error envelope | Every error returns documented envelope shape |
| 10 | First test suite | pytest-asyncio, httpx client, factories | ~30 tests green covering steps 3–9 |

## V2 — Real Application

| # | Step | Concepts learned |
|---|---|---|
| 11 | RBAC dependency chain | `get_current_membership`, `require_permission`, permission matrix enforcement |
| 12 | Organization members | Add/remove/role-change endpoints; last-owner rule |
| 13 | Project members | Visibility rules for MEMBERs; many-to-many patterns |
| 14 | Comments | Nested resources; author-only deletion |
| 15 | Activity log | Append-only audit table written transactionally |
| 16 | Filtering / pagination / search | Query param models, whitelisted ordering, pagination envelope |

## V3 — Production Features

| # | Step | Concepts learned |
|---|---|---|
| 17 | Redis + Celery setup | Brokers, workers as separate processes |
| 18 | Notifications on assignment/comment | Enqueue-after-commit pattern |
| 19 | Email pipeline | Templates, dev console transport, retries/backoff |
| 20 | Periodic jobs (beat) | Deadline reminders, token cleanup |

## V4 — Engineering

| # | Step | Concepts learned |
|---|---|---|
| 21 | Docker Compose full stack | Multi-container orchestration |
| 22 | GitHub Actions CI | Lint + migrate + test on every push |
| 23 | Structured logging + request IDs | Correlating a request across logs |
| 24 | Coverage push to targets | Testing the security matrix exhaustively |
| 25 | NGINX deployment | Reverse proxy, TLS basics |

## V5 — Scale

| # | Step | Concepts learned |
|---|---|---|
| 26 | Rate limiting | Token bucket, per-IP vs per-user |
| 27 | Indexes + query optimization | EXPLAIN ANALYZE, N+1 elimination (`selectinload`) |
| 28 | Concurrency control | Optimistic locking on task updates (`updated_at` version check) |
| 29 | Idempotency keys | Safe retries for POST operations |
| 30 | Load testing | locust/k6, finding real bottlenecks |

## Milestone Interview Questions

After each version you should answer these without notes:

**V1:** Why is the refresh token stored hashed? What happens when a JWT expires mid-session?
Why does registration hash the password *before* any DB insert?

**V2:** Where exactly does tenancy isolation happen, and what defends it if a developer
writes a new repository function wrong? Walk me through what a MEMBER can and cannot do.

**V3:** Why enqueue after commit? A worker dies mid-email-batch — what happens to those emails?

**V4:** Your CI failed on a PR that "only changed docs" — plausible? How would you find
which request caused a production error?

**V5:** Two managers assign the same task simultaneously — what happens? Show me the slowest
query in your system and how you know.
