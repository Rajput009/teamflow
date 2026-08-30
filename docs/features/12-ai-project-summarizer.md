# Feature: AI Project Summarizer (AI V2)

> Second feature of the AI layer. Pure READ: aggregates come from
> PostgreSQL queries (structured data stays structured), the LLM only turns
> them into a narrative. No proposal, no approval gate — nothing is written.

## 1. What problem am I solving?

A manager opening a project with 120 tasks and hundreds of activity entries
cannot answer "how is this going?" without manual archaeology across filters,
boards and logs. TeamFlow computes the ground-truth statistics itself and asks
the LLM to narrate them — so the numbers are always real database facts and
only the prose is generated. This is the "DB + business logic + AI" pattern:
the model never invents statistics, it interprets them.

## 2. What data do I need?

All computed server-side from existing tables (no new tables, no migration):

| Statistic | Source |
|---|---|
| total tasks, counts per status | GROUP BY status on tasks of the project |
| progress_pct | completed / total |
| overdue | `due_date < today(UTC)` AND status != COMPLETED |
| unassigned_high_urgent | priority HIGH/URGENT AND assigned_to IS NULL |
| due_within_week | due_date in [today, today+7], not COMPLETED |
| stale_days_threshold | open tasks (not COMPLETED) with `updated_at < now - 5 days` |
| workload_top | open tasks per assignee (top 5, joined email) |
| recent_activity | last 8 activity rows for the project (action + timestamp) |

LLM input: a compact JSON block of exactly these facts. LLM output: freeform
markdown prose (no JSON contract needed — there is nothing structured to
validate; emptiness is the only failure).

## 3. What API endpoint do I need?

```text
GET /api/v1/ai/projects/{project_id}/summary   → 200
{
  "project_id": "...",
  "project_name": "E-commerce Platform",
  "stats": {
    "total_tasks": 42,
    "status_counts": {"COMPLETED": 18, "IN_PROGRESS": 9, ...},
    "progress_pct": 43,
    "overdue_count": 4,
    "unassigned_high_urgent_count": 2,
    "due_within_week_count": 6,
    "stale_open_tasks_count": 3,
    "workload": [{"email": "ali@team.com", "open_tasks": 11}, ...],
    "recent_activity": [{"action": "task.completed", "created_at": "..."}]
  },
  "summary": "## Status\n\nDevelopment is roughly 43% complete..."
}
```

GET chosen deliberately over POST: the operation is a pure read with no side
effects. Cost/caching concerns are noted as future work (response caching),
not semantics.

## 4. What should the database do?

Read-only aggregation queries on `tasks`, `activities`, `users` — all scoped
through the existing visibility chain (`get_accessible`). The LLM receives
only derived aggregates, never raw rows beyond tiny activity samples, and
never any data outside the caller's org.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| No API key | 503 | `AI_NOT_CONFIGURED` |
| Provider timeout / HTTP error | 502 | `AI_UPSTREAM_ERROR` |
| Model returns empty/whitespace text (after 1 retry) | 502 | `AI_INVALID_OUTPUT` |
| Invisible / foreign-org project | 404 | indistinguishable (existing rule) |
| Empty project (0 tasks) | 200 — stats are zeros; prompt notes the emptiness |

## 6. Who is allowed to perform this operation?

Anyone who can SEE the project — same visibility chain as reading task lists:
MANAGER+ always; project MEMBERs via project_members. No new permissions;
the summarizer cannot leak what its caller couldn't already read.

## 7. How do I test it?

FakeLLMClient throughout (no network):

1. Stats correctness: seed tasks across statuses/due dates/assignees → assert each stat field against known values.
2. Progress math: 3 of 4 completed → 75.
3. Narrative present and non-empty; stats echoed verbatim from DB facts.
4. Cross-org project → 404 indistinguishable.
5. Missing key → 503 AI_NOT_CONFIGURED.
6. Provider error / empty replies twice → 502 codes.
7. Empty project → 200 with zeroed stats.
8. Stale detection: task untouched for 6 days counted, fresh one not.
