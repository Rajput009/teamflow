# Feature: AI Task Generator (AI V1)

> The first feature of TeamFlow's AI layer. Architecture rule for ALL AI
> features: **the LLM never touches the database**. It produces *validated
> proposals*; a human approves; existing services persist them through the
> normal permission-checked paths.

## 0. Architecture

```text
User idea / task
      │
      ▼
POST /ai/projects/drafts  ──►  AiService
      │                           │  1. build prompt (app/ai/prompts.py)
      │                           │  2. call LLM via LLMClient protocol
      │                           │  3. parse JSON → Pydantic validate
      │                           │  4. one retry feeding validation errors back
      │                           │  5. business-rule sanity caps
      │                           ▼
      │                     PROPOSAL (plain JSON response — NOT persisted)
      ▼
Human edits / accepts
      │
      ▼
POST /projects/from-drafts ──► re-validates EVERYTHING (proposal = untrusted
                                input) → ProjectService/TaskService persist →
                                activity audit rows
```

Provider abstraction: `LLMClient` protocol in `app/ai/llm.py`. First adapter =
OpenAI-compatible HTTP client (`httpx`) pointed at any compatible endpoint
(OpenRouter by default). `FakeLLMClient` replaces it in tests via dependency
override — CI never touches the network.

## 1. What problem am I solving?

Managers lose 20–40 minutes translating "we're building X" into a well-formed
project skeleton: naming tasks, ordering subtasks, guessing priorities. TeamFlow
turns a plain-language idea into a complete draft project (tasks + subtasks +
priorities + suggested owners), and turns an existing task into suggested
subtasks — but every write still goes through a human and the same business
rules as manual creation.

This is also the portfolio thesis of the AI layer: structured output validation,
human-in-the-loop approval, and authorization-aware orchestration — not a
chatbot bolted onto an API.

## 2. What data do I need?

### Inputs

| Field | Endpoint | Rules |
|---|---|---|
| `idea` | POST /ai/projects/drafts | str, 10–4000 chars after strip |
| `title_hint` | POST /ai/projects/drafts | optional str ≤ 255 |
| `instruction` | POST /ai/tasks/{id}/breakdowns | str, 5–2000 chars |

### Proposal schemas (LLM output contracts, `app/ai/schemas.py`)

```text
ProjectProposal:
  name: str (1–255)
  description: str | None (≤ 5000)
  tasks: list[GeneratedTask]        # service caps at ai_max_generated_tasks (30)

GeneratedTask:
  title: str (1–255)
  description: str | None (≤ 2000)
  priority: LOW|MEDIUM|HIGH|URGENT  # defaults MEDIUM
  due_in_days: int | None           # relative days from today; 0–365
  subtasks: list[str]               # titles only, ≤ 10 each, ≤ 255 chars
  suggested_owner_email: str | None # resolved at ACCEPTANCE time only

TaskBreakdown:
  subtasks: list[GeneratedTask-like]  # titles required, rest optional, ≤ 15
```

Validation happens twice by design: once on LLM output (drafting), and AGAIN
on acceptance — the client posts the proposal back, so the accept endpoint
treats it exactly like any other untrusted request body.

## 3. What API endpoint do I need?

| Method | Path | Success | Notes |
|---|---|---|---|
| POST | `/api/v1/ai/projects/drafts` | 200 ProjectProposalResponse | nothing persisted |
| POST | `/api/v1/projects/from-drafts` | 201 ProjectResponse (+tasks created) | the real write |
| POST | `/api/v1/ai/tasks/{task_id}/breakdowns` | 200 TaskBreakdownResponse | nothing persisted |
| POST | `/api/v1/tasks/{task_id}/accept-breakdowns` | 201, items: created subtasks | the real write |

Draft request example:

```json
POST /api/v1/ai/projects/drafts
{ "idea": "E-commerce website for a clothing company: auth, products,
   payments, orders, admin dashboard, deployment.",
  "title_hint": "E-commerce Platform" }
```

Accept request example (client echoes back — possibly edited):

```json
POST /api/v1/projects/from-drafts
{ "name": "E-commerce Platform",
  "description": "...",
  "tasks": [ { "title": "Authentication", "priority": "HIGH",
               "due_in_days": 14,
               "subtasks": ["Registration", "Login", "Password reset"],
               "suggested_owner_email": "ali@team.com" } ] }
```

Acceptance response includes a `warnings` array, e.g. unknown owner emails
that were dropped: `{"warnings": ["No org member with email ghost@x.com —
owner suggestion ignored"]}`.

## 4. What should the database do?

- Draft endpoints: **zero writes**.
- Acceptance reuses existing services → existing tables/constraints
  (`projects`, `tasks`, unique name index, FK checks). No new tables, no
  migration.
- Activity rows (new ActionType values, plain string column — no migration):
  - `ai.project_created` — when a draft is accepted into a real project
    (`new_value`: {name, task_count})
  - `ai.tasks_generated` — when a breakdown is accepted
    (`new_value`: {parent_task_id, count})

**Flat persistence decision:** TeamFlow's data model has no task hierarchy
(no `parent_task_id` column). Proposed subtasks are therefore persisted as
first-class sibling tasks inheriting the parent's priority/due date/owner.
"Hierarchy" is a draft-time presentation concept in V1; a real tree lands
only if/when the schema grows a parent column.
- Assignee resolution at acceptance: `suggested_owner_email` → membership
  lookup within the actor's org ONLY. Unknown emails never error — they are
  dropped with a warning (the LLM hallucinating an address must not fail the
  whole operation).

Transaction boundary: one acceptance = one transaction creating project +
all tasks + activity row, committed atomically by `get_db`.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| No API key configured | 503 | `AI_NOT_CONFIGURED` — surfaces only at generation time (lazy client), so validation and authorization errors always take precedence |
| LLM HTTP error / timeout / non-JSON | 502 | `AI_UPSTREAM_ERROR` |
| LLM output fails Pydantic validation even after 1 feedback retry | 502 | `AI_INVALID_OUTPUT` |
| idea/instruction length invalid | 422 | standard envelope |
| Generated > cap tasks | silently truncated to `ai_max_generated_tasks`, noted in proposal metadata |
| Accept: duplicate project name in org | 409 | `PROJECT_NAME_EXISTS` (existing) |
| Accept: caller demoted between draft & accept | 403 | `FORBIDDEN` (re-checked) |
| Breakdown on invisible task | 404 | indistinguishable (existing tenancy rule) |
| Accept breakdown on deleted task | 404 | `NOT_FOUND` |
| Unknown `suggested_owner_email` | — | dropped + warning (never 500) |
| `due_in_days` → past date | clamped to today+1 at acceptance |

Deliberately out of scope for V1: rate limiting per user (V5), token/cost
accounting, streaming responses, conversation memory.

## 6. Who is allowed to perform this operation?

| Operation | Permission |
|---|---|
| Generate project draft | MANAGER+ (org role) |
| Accept project draft | MANAGER+ (same rules as manual project create) |
| Generate task breakdown | anyone who can SEE the task (visibility chain incl. project-membership narrowing) |
| Accept task breakdown | MANAGER+ or MEMBER assigned to the parent task (same rule as manual task create/update) |

The AI layer NEVER grants powers the user didn't have: proposals execute
through the exact same service methods manual flows use.

## 7. How do I test it?

All tests run against `FakeLLMClient` (dependency override returns canned,
scriptable responses). No network, no key.

Happy paths:
1. Draft from idea returns 200 + valid proposal shape; DB unchanged afterwards.
2. Accepting a draft creates project + tasks + subtasks with correct priorities/due dates; response 201.
3. Breakdown returns suggestions; accepting creates real subtasks under the parent task.
4. `due_in_days` converts to concrete future dates; warnings reported for dropped unknown owners.

Failure modes:
5. Missing API key → 503 AI_NOT_CONFIGURED.
6. FakeLLMClient raises timeout/HTTP error → 502 AI_UPSTREAM_ERROR.
7. FakeLLMClient returns garbage JSON twice → 502 AI_INVALID_OUTPUT.
8. First response invalid, second valid → succeeds (retry loop works).
9. Over-long idea / instruction → 422.

Security:
10. MEMBER calling project-draft → 403.
11. Cross-org task id for breakdown → 404 (indistinguishable).
12. Accepting a doctored proposal (100 tasks) truncates to cap.
13. Duplicate project name at accept → 409 PROJECT_NAME_EXISTS.
14. Activity rows written exactly once per accepted operation.
