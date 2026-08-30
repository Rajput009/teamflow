# Feature: Projects CRUD

## 1. What problem am I solving?

Work is organized into projects. This is the first entity where **tenancy scoping**
becomes visible: projects belong to an organization, and the entire codebase must
prove — with tests — that Org B can never read, modify, or even detect Org A's projects.

## 2. What data do I need?

| Field | Type | Rules |
|---|---|---|
| name | string | Required, 1–255, unique within the org |
| description | string | Optional |
| deadline | date (ISO) | Optional, not in the past at creation |

Path params for detail routes: `project_id` (UUID).
Entities: `projects` (+ `memberships` for role checks, `project_members` from V2 for visibility).

## 3. What API endpoint do I need?

| Method | Path | Min role | Success |
|---|---|---|---|
| POST | `/api/v1/projects` | MANAGER | 201 + project |
| GET | `/api/v1/projects` | MEMBER | 200 paginated list (visible subset for MEMBERs in V2) |
| GET | `/api/v1/projects/{id}` | MEMBER (if visible) | 200 + project |
| PATCH | `/api/v1/projects/{id}` | ADMIN, or MANAGER who created it | 200 + updated |
| DELETE | `/api/v1/projects/{id}` | ADMIN, or MANAGER who created it | 204 |

## 4. What should the database do?

- INSERT with `organization_id` taken from the **authenticated membership**, never the client.
- `UNIQUE (organization_id, name)` → duplicate name inside one org is a 409;
  the same name in a different org is fine.
- All reads/writes go through scoped queries: `WHERE id = :id AND organization_id = :org`.
- DELETE cascades to tasks/comments/project_members.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Project id exists but belongs to another org | 404 | `NOT_FOUND` (deliberately — see RBAC doc) |
| Project doesn't exist at all | 404 | `NOT_FOUND` (same response — indistinguishable) |
| Role too low (MEMBER creating) | 403 | `FORBIDDEN` |
| Duplicate name within org | 409 | `PROJECT_NAME_EXISTS` |
| Malformed UUID / invalid body / past deadline | 422 | `VALIDATION_ERROR` |

## 6. Who is allowed to perform this operation?

Create: MANAGER+. Update/delete: ADMIN+ or the MANAGER who created that specific
project ("➖ creator only" cell of the matrix). Read: all members (visibility
narrowing for MEMBERs arrives with project membership in V2).

## 7. How do I test it?

1. MANAGER creates project → 201; `organization_id` matches their membership.
2. Org B user fetches Org A's project by id → 404, same body as nonexistent id.
3. Org B user lists projects → none of Org A's appear.
4. MEMBER creates project → 403; MANAGER creates → 201.
5. MANAGER edits a project they didn't create → 403; edits own → 200.
6. Duplicate name in same org → 409; same name in other org → allowed.
7. Deadline in the past → 422.
8. Deleting a project removes its tasks (verify via DB count).
