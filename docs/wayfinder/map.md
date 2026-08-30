# Wayfinder Map: TeamFlow AI V3–V5 + Docker — Design Decisions

**Status:** all decisions resolved — ready to hand off for TDD implementation

## Destination

Resolve the open *design/architecture* decisions for TeamFlow's remaining journey
(AI V3 Project Chat, V4 Risk Detection, V5 Agent, Docker finale) as a written
decision record, ready to hand off for implementation. No feature code is written
in this effort.

## Notes

- **Domain context:** `docs/` (spec + feature docs 01–12), `app/ai/*` (LLMClient
  protocol, AiService, human-in-the-loop two-step, validated output),
  `app/repositories/*`, `app/services/*`. Consult skills per ticket: **grilling** +
  **domain-modeling**.
- **Standing preferences every decision must honor:** AI never writes to the DB
  directly; LLM output is validated as untrusted input and re-run through existing
  services on acceptance; optimistic locking on tasks; tests fully mocked
  (`ScriptableLLM`); `ruff` clean; suite green; `UnconfiguredLLMClient` laziness
  (auth/tenancy errors precede "AI off").
- **Hand-off effort uses TDD (hard requirement):** implementation runs red → green
  at agreed seams (AI endpoints, `AiService` methods, repository aggregates); tests
  live at public boundaries only; no horizontal slicing; refactor belongs to review,
  not the loop.

## Decisions so far

<!-- index: one line per closed ticket, gist + link. Populated as tickets resolve. -->

- [Research — LLM provider capabilities](tickets/WF-7-llm-capabilities.md): OpenRouter supports streaming (SSE), native tool/function calling, and JSON/structured output across most models; recommend `google/gemini-2.5-flash-lite` for chat, `gpt-4o-mini`/`gemini-2.5-flash` for narration/tool-use — findings in `research/llm-capabilities.md`.
- [AI V3 Project Chat — context assembly & grounding](tickets/WF-1-project-chat-context.md): read-only, stateless, non-streaming-first chat; membership-gated, narration-grounded over project/tasks/activity/comments context; no citations or RAG in V3; `AiService.chat` + new endpoint.
- [AI V4 Risk Detection — risk ruleset & LLM narration scope](tickets/WF-2-risk-ruleset.md): core-five DB-computed `RiskSignal`s in `app/ai/risk.py`, severity deterministic (LLM never sets it), narration-only LLM; `GET /ai/projects/{id}/risks` returns computed+prose split.
- [Backgrounding AI calls — when/where AI leaves the request path](tickets/WF-4-backgrounding.md): V3/V4 stay sync; only V5 agent + future scans backgrounded via Celery; job-id + poll contract; failed-state carries AI errors; `task_always_eager` flip transparent to endpoints.
- [Docker finale — compose topology, broker flip, beat, secrets](tickets/WF-5-docker-topology.md): one Dockerfile for api/worker/beat + postgres/redis + optional nginx (TLS deferred); Redis broker; `alembic upgrade head` in api entrypoint; beat runs deadline-reminders + token-cleanup; `TASK_ALWAYS_EAGER` env flip, tests stay eager.
- [AI V5 Agent — tool whitelist & approval gate](tickets/WF-3-agent-tools.md): safe whitelist (create/assign/update_task/add_comment + read summarize) in `app/ai/tools.py`; explicit per-action approval; bound by caller's permissions; parsed-JSON proposals validated as untrusted; propose backgrounded, approve inline.
- [Cross-cutting — rate limiting, CORS, notification dedupe/outbox](tickets/WF-6-cross-cutting.md): per-user Redis token-bucket on AI routes (flag + `AI_RATE_LIMIT_PER_MINUTE`); `CORS_ORIGINS` explicit allowlist (default empty); notification idempotency-key dedupe now, outbox deferred.

## Not yet specified

<!-- in-scope fog not yet sharp enough to ticket; graduates as frontier advances -->

_No remaining fog._ All open decisions are resolved (WF-1…WF-7 closed). The only items
once dimly visible — exact prompt text, schema field names, migration details — are
**execution-level build slices**, explicitly out of scope for this decisions-only
effort and owned by the later hand-off implementation.

## Out of scope

<!-- work ruled beyond the destination; never graduates -->

- Implementing any feature or writing its tests (that is the later hand-off effort).
- The already-completed AI V1 (Task Generator) and V2 (Summarizer).
- Production hardening beyond topology decisions (observability, autoscaling).
- Any frontend work.
