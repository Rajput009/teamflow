# Background Jobs (V3)

## Why a Queue at All?

When a manager assigns a task, three things happen:

1. Update the DB row — **must** be in the request (user needs confirmation).
2. Create an in-app notification — could be background.
3. Send an email — slow (100ms–2s), flaky (SMTP can fail), and irrelevant to the HTTP response.

If we do all three inline: every assignment takes seconds, and one SMTP hiccup
returns a 500 for an operation that actually succeeded.

```text
Manager assigns task
        ↓
FastAPI: update DB + enqueue job     ← request ends here (~50ms)
        ↓
Redis (broker)
        ↓
Celery worker: create notification, send email
```

**Rule:** the request commits the *state change*; the queue handles the *side effects*.
The activity row is the exception — it stays in the same transaction as the state
change so the audit trail can never diverge from reality (see 03-database.md).

## Topology

```text
┌───────────┐    enqueue     ┌───────┐    consume    ┌────────────────┐
│  FastAPI   │ ────────────► │ Redis │ ◄──────────── | Celery worker(s)│
└───────────┘                └───────┘               └───────┬────────┘
                                                             │
                                                        PostgreSQL
                                                    (notifications table)
```

- Redis = broker (and result backend). Not persisted-critical — losing queued emails is acceptable; losing committed DB writes is not.
- Workers run as separate containers (`docker compose` service), scaled independently from the API.

## Job Catalog

| Task name | Trigger | What it does | Retry policy |
|---|---|---|---|
| `send_email` | notification events | Render template, send via SMTP | 3 retries, exponential backoff |
| `create_notification` | task_assigned, comment_added, deadline approaching | INSERT notifications row | 3 retries |
| `send_deadline_reminders` | periodic (celery beat, daily) | Find tasks due in 24h → notify assignees | n/a (idempotent by design) |
| `cleanup_expired_tokens` | periodic (daily) | Delete stale refresh_token rows | n/a |

## Reliability Rules

1. **Enqueue after commit.** Jobs are enqueued only *after* the DB transaction commits
   (`session.commit()` then `task.delay(...)`), otherwise a rollback leaves phantom jobs.
   (V5 upgrade path: transactional outbox.)
2. **Idempotency.** Every worker task tolerates re-execution:
   - notifications deduped via `(recipient_id, type, entity_id, unread)` check;
   - emails include idempotency key in logs; duplicate sends are acceptable, duplicate DB rows are not.
3. **Retries with backoff:** `autoretry_for=(Exception,), retry_backoff=True, max_retries=3`.
   After final failure: log structured error (V4: alert).
4. **Timeouts:** every task declares `soft_time_limit`; a hung SMTP call must not pin a worker forever.
5. **Poison messages:** a task failing deterministically exhausts retries and is dropped with an error log — never an infinite loop.

## Email Design

- Templates in `app/workers/emails/templates/` (Jinja2): plain text first, HTML later.
- Development transport: console backend (emails printed to worker logs) — no real SMTP needed to develop/test.
- Production transport behind a settings flag (`email_backend: "console" | "smtp"`).

## What This Teaches

- Why synchronous side effects make APIs fragile and slow
- Broker vs worker vs result backend
- Idempotency and retry semantics
- The "commit state, defer effects" pattern
