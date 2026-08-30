# Feature: Activity Log (Audit Trail)

## 1. What problem am I solving?

Businesses need to answer "who changed what, when?" — for accountability, dispute
resolution, and debugging. Whenever something meaningful happens (project created,
task reassigned, status changed, member role updated), TeamFlow records an immutable
activity row.

## 2. What data do I need?

Per event: actor (from token), action name (`task.status_changed`,
`task.assigned`, `project.created`, `member.role_updated`, ...), entity type + id,
organization context, optional project/task references, and JSONB `old_value` /
`new_value` snapshots of the changed fields.

Entity: `activities` — append-only. **No update endpoint will ever exist.**

## 3. What API endpoint do I need?

| Method | Path | Min role | Notes |
|---|---|---|---|
| GET | `/api/v1/activities` | MEMBER* | org-wide timeline, newest first, paginated |
| GET | `/api/v1/projects/{id}/activities` | visible reader | per-project slice |

Filters: `?action=task.status_changed&actor_id=<uuid>&page=&limit=`.
Response: standard pagination envelope.

\* V2 simplification: org members read the whole org log; narrowing to
visible-projects-only arrives with real project membership enforcement in this same version.

## 4. What should the database do?

- INSERT inside the **same transaction as the state change** — if the change rolls
  back, its activity must roll back too. An audit trail that can diverge from reality
  is worse than none.
- Indexes: `(organization_id, created_at DESC)`, `(task_id, created_at DESC)`.
- Never UPDATE or DELETE from application code.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Foreign-org project activities | 404 | `NOT_FOUND` |
| Bad filters / pagination | 422 | `VALIDATION_ERROR` |
| Activity insert fails while change succeeds | impossible by design — same transaction; a failure aborts BOTH (500) | `INTERNAL_ERROR` |

## 6. Who is allowed to perform this operation?

Reading: org members (scoped as above). Writing: nobody directly — activities are
created internally by services as side effects of legitimate operations.

## 7. How do I test it?

1. Create task → activity `task.created` exists with correct actor.
2. Change status → one new row with old_value/new_value snapshots.
3. Reassign → `task.assigned` row records both user ids.
4. Failed operation (403) → NO activity row (rolled back with transaction).
5. Org-wide list ordered newest-first; project-scoped list excludes other projects' events.
6. Cross-org project activities → 404 envelope.
