# Feature: Notifications (V3)

## 1. What problem am I solving?

When someone assigns you a task or comments on your work, you shouldn't have to
poll the whole system to notice. TeamFlow generates in-app notifications for these
events — delivered asynchronously so the triggering API call stays fast and a slow
side-effect can never fail the main operation.

## 2. What data do I need?

| Field | Source | Rules |
|---|---|---|
| recipient_id | derived (assignee / task assignee) | never the actor themselves |
| type | enum | `TASK_ASSIGNED`, `COMMENT_ADDED` |
| payload | JSONB | render data: task title, project id, actor name |
| read_at | null | set when user marks read |

Entity: `notifications` (per 03-database.md).

## 3. What API endpoint do I need?

| Method | Path | Success |
|---|---|---|
| GET | `/api/v1/notifications` | 200 paginated own notifications (`?unread=true`) |
| POST | `/api/v1/notifications/{id}/read` | 204 |
| POST | `/api/v1/v1/notifications/read-all` | 204 |

## 4. What should the database do?

- INSERT by the worker, SELECT/marked-read by the owner.
- Index `(recipient_id, read_at)` backs "unread count" style queries.
- Rows belong to their recipient only — no org scoping needed (personal inbox).

## 5. What can go wrong?

| Failure | Handling |
|---|---|
| Broker down at enqueue | eager/dev: impossible (inline). prod: enqueue-after-commit means the state change still succeeded; job lost → acceptable for notifications (not for audit). |
| Worker crash mid-task | Celery retry ×3, exponential backoff; notification insert is idempotent-enough (duplicate notification ≪ missing one) |
| Unknown recipient (deleted user) | task logs and exits cleanly — no retry loop |
| Notification for actor's own action | suppressed at trigger site |

## 6. Who is allowed to perform this operation?

Reading/marking-read: strictly the recipient — notifications are a personal inbox,
resolved from the token, no path-based user ids anywhere.

## 7. How do I test it?

1. Assigning a task creates a notification for the assignee (visible via GET).
2. Self-assignment produces NO notification.
3. Commenting on an assigned task notifies the assignee; commenting on your own task doesn't.
4. `unread=true` filter and read-all work; marking another user's notification read → 404.
5. Eager-mode guarantee: notification EXISTS immediately after the 200 response.
