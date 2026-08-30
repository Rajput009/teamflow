# Feature: Task Comments

## 1. What problem am I solving?

Task work is collaborative: "finished the JWT part, need review on refresh rotation."
Comments give tasks a discussion thread, stored with the task and visible to everyone
who can see the task.

## 2. What data do I need?

| Field | Source | Rules |
|---|---|---|
| task_id | path | must be visible to actor (tenancy JOIN via project) |
| content | body | 1–5000 chars, trimmed |

Entity: `comments` (id, task_id FK cascade, author_id FK restrict, content, timestamps).
Author is derived from the token — never accepted from the client.

## 3. What API endpoint do I need?

| Method | Path | Min role | Success |
|---|---|---|---|
| GET | `/api/v1/tasks/{task_id}/comments` | visible to reader | 200 list (chronological) |
| POST | `/api/v1/tasks/{task_id}/comments` | MANAGER+, or MEMBER assigned/visible* | 201 |
| DELETE | `/api/v1/comments/{comment_id}` | author only; ADMIN+ any | 204 |

\* V1 simplification continues: all org members count as project participants until
per-project membership narrows this.

## 4. What should the database do?

- INSERT comment; index `(task_id, created_at)` backs chronological loading.
- `author_id` FK has **no cascade** — deleting users must not silently rewrite history.
- DELETE is a hard delete of one row (edit not offered in V2).

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Task foreign/nonexistent | 404 | `NOT_FOUND` |
| Comment nonexistent / in another org's task | 404 | `NOT_FOUND` |
| Empty or oversized content | 422 | `VALIDATION_ERROR` |
| Deleting someone else's comment as MEMBER/MANAGER | 403 | `FORBIDDEN` |

## 6. Who is allowed to perform this operation?

Read/write: anyone who can read the task. Delete: the comment's AUTHOR, or ADMIN+
(moderation). Tenancy applies through the same project JOIN as tasks.

## 7. How do I test it?

1. Post comment → 201, author set from token, returned chronologically.
2. List comments for foreign-org task → 404.
3. MEMBER posts on visible task → 201.
4. Author deletes own comment → 204; other MEMBER deletes it → 403; ADMIN deletes → 204.
5. Cross-org comment delete by ID → 404 (not 403!).
6. Empty content → 422.
