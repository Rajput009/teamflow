---
id: WF-4
title: Backgrounding AI calls — when/where AI leaves the request path
type: grilling
labels: [wayfinder:grilling]
assignee: opencode
status: closed
blocking: [WF-5]
blocked_by: []
asset: null
---

## Question

When should AI calls move off the HTTP request path into Celery?

The roadmap notes "AI calls move to background jobs then too" during the Docker
phase. This decision fixes *when*, which shapes endpoint contracts
(polling vs. webhook vs. streaming) and rate-limiting.

Sub-questions:

1. **Sync-through-V3/V4?** Keep V3 chat and V4 risk synchronous (running on eager
   Celery / inline) so the human gets an immediate response, and only background
   long-running V5 agent runs? Or background everything at the Docker boundary?
2. **If backgrounded earlier** — what's the client contract? Job id + poll
   endpoint? Webhook/callback? SSE? (Interacts with WF-1 streaming decision.)
3. **Failure/timeout semantics** — how do we surface LLM timeouts/upstream errors
   (`AiUpstreamError` 502, `AiNotConfiguredError` 503) from a background job to the
   user?
4. **Eager→broker flip safety** — how does flipping `task_always_eager=False`
   (ticket 5) stay transparent to endpoints designed assuming sync?

Outcome: a clear policy (which features are sync vs. backgrounded, and the client
contract for backgrounded ones) that WF-5 implements.

## Resolution

Decided policy for **when AI leaves the request path**.

- **What gets backgrounded:** V3 chat and V4 risk stay **synchronous** (single LLM call
  on the request path, eager Celery in dev). Only **V5 agent runs** (multi-step,
  potentially long) and any future scheduled scans are backgrounded via Celery.
- **Client contract (backgrounded):** `POST` kicks a Celery job, returns `{job_id}`;
  `GET /ai/jobs/{job_id}` polls status + result/error. Chosen over SSE for simplicity
  and full testability with `ScriptableLLM`.
- **Failure surfacing:** backgrounded jobs catch `AiUpstreamError`/`AiNotConfiguredError`
  and record a `failed` state carrying the error; the poll endpoint returns it. Sync
  endpoints unchanged (502/503 directly).
- **Eager→broker flip:** transparent. Sync endpoints `await` inline; backgrounded ones
  poll a job. Tests stay on `task_always_eager=True`; the flip is a
  `task_always_eager` env toggle owned by WF-5. No endpoint break.
- **Job lifecycle:** states `pending → started → success | failure` via Celery's Redis
  result backend (reuse `redis_url`). Output/error is the job's return value; available
  synchronously under eager/test mode.
- **Poll endpoint & ownership:** `GET /ai/jobs/{job_id}` requires auth
  (`get_current_user`); the kicking task records the owner user id; poll verifies
  ownership and returns 403/404 for others. Keeps backgrounded AI results private.
- **Config:** no new backgrounding-specific `Settings`; reuse `redis_url` +
  `task_always_eager`; optional `AI_JOB_TTL` constant for result expiry.
