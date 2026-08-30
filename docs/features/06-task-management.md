# Feature: Task Management & Assignment

## 1. What problem am I solving?

Projects decompose into tasks with status, priority, deadline, and an assignee.
Assignment is the feature's core workflow — it connects users to work and (in V3)
triggers the first background job: a notification. It also introduces cross-entity
validation: you may only assign to someone who belongs to the task's organization.

## 2. What data do I need?

Create/update task:

| Field | Type | Rules |
|---|---|---|
| title | string | Required, 1–255 |
| description | string | Optional |
| status | enum | `TODO` `IN_PROGRESS` `IN_REVIEW` `COMPLETED` (default TODO) |
| priority | enum | `LOW` `MEDIUM` `HIGH` `URGENT` (default MEDIUM) |
| due_date | date | Optional |

Assign: path `task_id` + body `user_id`.
Entities: `tasks`, `projects` (parent), `memberships` (assignee validation).

## 3. What API endpoint do I need?

| Method | Path | Min role | Success |
|---|---|---|---|
| POST | `/api/v1/projects/{id}/tasks` | MANAGER, or MEMBER of that project | 201 + task |
| GET | `/api/v1/projects/{id}/tasks` | MEMBER (visible) | 200 paginated + filters (`status`,`priority`,`assigned_to`,`search`,`ordering`) |
| GET | `/api/v1/tasks/{id}` | MEMBER (visible) | 200 + task (+ assignee summary) |
| PATCH | `/api/v1/tasks/{id}` | MANAGER+, or MEMBER if assigned to them | 200 + updated |
| DELETE | `/api/v1/tasks/{id}` | MANAGER+ | 204 |
| POST | `/api/v1/tasks/{id}/assign` | MANAGER | 200 + task with assignee set |

## 4. What should the database do?

- INSERT task with `project_id`; org context derived via the project row.
- Indexes backing list queries: `(project_id)`, `(project_id, status)`, `(assigned_to_id)`.
- Assign: UPDATE `assigned_to_id` after service validates the target holds an
  active membership in the task's org. DB FK alone can't express this rule —
  that's why the service layer exists.
- Filters translate to parameterized WHERE clauses; ordering uses a **whitelist**
  of columns (`due_date`, `priority`, `created_at`) — never interpolated input.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Task in another org / nonexistent | 404 | `NOT_FOUND` |
| Assignee not a member of the task's org | 422 | `USER_NOT_ORG_MEMBER` |
| MEMBER tries to assign | 403 | `FORBIDDEN` |
| MEMBER patches a task not assigned to them | 403 | `FORBIDDEN` |
| Invalid enum value / bad UUID | 422 | `VALIDATION_ERROR` |

## 6. Who is allowed to perform this operation?

Create/assign/delete: MANAGER+. Update fields: MANAGER+, or a MEMBER **only for
tasks assigned to them** (self-service status updates). Read: per project visibility.

## 7. How do I test it?

1. Create task → 201 with defaults (TODO / MEDIUM).
2. Filter `?status=IN_PROGRESS&priority=URGENT` returns exactly matching rows.
3. `?ordering=-due_date` sorts correctly; unknown ordering field → 422.
4. Pagination envelope correct at boundary sizes (0 items, exact multiple of limit).
5. Assign to org member → 200, `assigned_to_id` persisted.
6. Assign to user from another org → 422 `USER_NOT_ORG_MEMBER` — the flagship security test.
7. MEMBER updates own task status → 200; updates unassigned task → 403.
8. MEMBER assigns → 403.
9. Task in Org A fetched by Org B member → 404.
