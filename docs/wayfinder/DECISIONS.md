# TeamFlow — AI V3–V5 + Docker: Consolidated Design Decisions

This document is the hand-off package for the remaining TeamFlow journey:
**AI V3 Project Chat, V4 Risk Detection, V5 Agent, and the Docker finale.**
It is the resolved decision record from the Wayfinder map
(`docs/wayfinder/map.md` + `docs/wayfinder/tickets/WF-*.md`). No feature code is
written here — implementation happens in a later TDD effort.

## Standing constraints (apply to every decision)

- AI never writes to the DB directly; LLM output is validated as untrusted input and
  re-run through existing services on acceptance.
- Optimistic locking on tasks (`version_id_col`); lost races → 409.
- Tests fully mocked (`ScriptableLLM`); the suite never touches the network.
- `ruff` clean; suite green after every change.
- `UnconfiguredLLMClient` laziness: auth/tenancy/validation errors always precede
  "AI is off" (503).
- Hand-off implementation uses **TDD** at agreed seams: red → green, one vertical
  slice per cycle, tests at public boundaries only.

## Research findings (inform the design)

- **LLM provider capabilities** (`docs/wayfinder/research/llm-capabilities.md`):
  OpenRouter supports streaming (SSE), native tool/function calling, and
  JSON/structured output across most models. Recommended: `google/gemini-2.5-flash-lite`
  for chat, `gpt-4o-mini` / `gemini-2.5-flash` for narration/tool-use.
- **Docker topology** (`docs/wayfinder/research/docker-topology.md`): conventional
  FastAPI + Celery + Redis + Postgres + nginx layout; `task_always_eager` flips via env
  and is transparent to endpoints.

---

## AI V3 — Project Chat (WF-1)

A **read-only, grounded Q&A** surface over a project's data.

- **Read-only:** chat never mutates; proposing actions deferred to V5.
- **Context:** project + members, tasks (status/assignee/due/priority), recent activity
  (~20), recent comments (~20) — all scoped to `organization_id`.
- **Bounding:** recency window + hard token cap; large projects fall back to aggregation
  summaries (reuse V2 `TaskRepository` aggregates).
- **Stateless:** `POST /ai/projects/{id}/chat` with `{question, history?}`; client holds
  history. Persisted chat tables deferred.
- **Transport:** non-streaming first (JSON); SSE is a later enhancement.
- **Grounding:** narration-only (V2 pattern); says "I don't know" when answer isn't in
  context; no tool access.
- **Schema:** request `{question, history?}`; response `{answer, model}`. Citations and
  RAG deferred.
- **Auth:** active project membership (`get_current_membership`); non-members 404.
- **Code:** `AiService.chat(organization_id, project_id, question, history)` reusing
  `_narrative_call`; endpoint wired with `get_current_membership` + `get_generating_service`.
- **Errors:** 503 (after membership guard), 502, 422. No new types.
- **Config:** no new `Settings`; `AI_CHAT_MAX_HISTORY_MESSAGES = 10` + chat prompt as
  constants in `app/ai/prompts.py`.

## AI V4 — Risk Detection (WF-2)

DB-computed risks, LLM narrates only.

- **Core-five signals:** overdue, single-owner bus-factor, unbalanced workload,
  stalled-open, unassigned high/urgent. (Scope-creep & dependency pile-ups deferred.)
- **Rules module:** `app/ai/risk.py` queries repos → typed `RiskSignal` objects.
- **LLM scope:** narration-only; receives computed signals, writes impact +
  per-signal recommendation; never invents risks or sets severity.
- **Output:** `{ risks: [{kind, severity, evidence, recommendation}], narrative: str }`
  (computed + prose split, like V2).
- **Severity:** deterministic thresholds in the rules module; `Severity` enum
  `low|medium|high`.
- **Trigger:** on-demand `GET /ai/projects/{id}/risks` (membership-gated); scheduled
  scanning deferred to WF-4/WF-5.
- **Code:** `AiService.risk_assessment(organization_id, project_id)` → rules + narration;
  threshold constants in `app/ai/risk.py`. Errors reuse 503/502/404/422.

## AI V5 — Agent (WF-3)

Tools with a human approval gate.

- **Tool whitelist:** `create_task`, `assign_task`, `update_task_status`, `add_comment`,
  plus read-only `summarize_project` (reuses V2). Thin wrappers over existing services;
  destructive tools deferred.
- **Approval gate:** explicit per-action approval (two-step), mirroring V1 draft→accept.
  Agent proposes; human approves/rejects each before execution.
- **Authorization:** every tool calls the existing service method → `permissions.py`
  guards apply. Agent is bound by the caller's role/tenancy.
- **Sandboxing/audit:** approved calls run through normal services → activity log,
  transactions, optimistic locking. Failed tool call aborts the plan.
- **Mechanism:** parsed-JSON proposals validated as untrusted input; native `tool_calls`
  not trusted.
- **Endpoints:** `POST /ai/projects/{id}/agent` `{instruction}` → Celery job → `{job_id}`;
  `GET /ai/jobs/{job_id}` polls proposed actions; `POST /ai/projects/{id}/agent/approve`
  executes approved subset. Membership-gated.
- **Registry:** `app/ai/tools.py` maps tool → `(service_method, required_permission,
  args_schema)`; explicit registration.
- **Proposal schema:** `{tool, args}` validated per-tool; unknown tool / schema failure
  rejected.
- **Propose vs approve:** propose backgrounded (slow), approve inline (fast).

## Backgrounding AI calls (WF-4)

- V3 chat and V4 risk stay **synchronous**; only V5 agent runs + future scans are
  backgrounded via Celery.
- **Contract:** `POST` → `{job_id}`; `GET /ai/jobs/{job_id}` polls status + result/error.
- **Failures:** backgrounded jobs catch AI errors → `failed` state carrying the error.
- **Flip:** `task_always_eager=False` via env is transparent to endpoints; tests stay
  eager.
- **Job lifecycle:** `pending → started → success|failure` via Celery Redis backend.
- **Poll ownership:** auth required; task records owner user id; poll enforces ownership.

## Docker finale (WF-5)

- **One Dockerfile** (`python:3.12-slim`) for `api`/`worker`/`beat` + `postgres:17` +
  `redis:7` + optional `nginx` (TLS deferred). `depends_on` with healthchecks.
- **Broker:** Redis (broker `/0`, result `/1`).
- **Alembic:** `alembic upgrade head` in the `api` entrypoint (idempotent); workers start
  after `api` healthy.
- **Beat:** `deadline-reminders` (hourly) + `token-cleanup` (daily) in `beat_schedule`.
- **Flip:** prod sets `TASK_ALWAYS_EAGER=False` + Redis URLs; tests stay eager (no broker,
  CI never hits network).
- **Secrets:** `.env` via `env_file`; never baked into image.
- **CI:** GitHub Actions — `ruff check` + mocked `pytest`; optional `docker compose build`.

## Cross-cutting (WF-6)

- **Rate limiting:** per-user Redis token-bucket on AI endpoints; `Settings` flag +
  `AI_RATE_LIMIT_PER_MINUTE`.
- **CORS:** explicit `CORS_ORIGINS` allowlist (default empty); never `*` with credentials.
- **Notifications:** idempotency-key dedupe now; full outbox/relay deferred.

---

## Implementation seams (for the TDD effort)

- `AiService.chat`, `AiService.risk_assessment`, `AiService` agent methods
- `app/ai/risk.py` (rules → `RiskSignal[]`)
- `app/ai/tools.py` (tool registry)
- Endpoints in `app/api/v1/endpoints/ai.py` (chat, risks, agent propose/approve, jobs)
- Celery tasks for V5 agent propose + beat schedules
- Docker: `Dockerfile`, `docker-compose.yml`, entrypoint, CI workflow

All tests at these public boundaries, mocked via `ScriptableLLM`; no network in CI.
