# Feature: Project Membership

## 1. What problem am I solving?

In V1 every org member implicitly saw every project — acceptable scaffolding, not a
real product. Companies need per-project access control: a designer added to the
website redesign shouldn't browse the payroll project. This feature replaces the
implicit rule with explicit `project_members` rows and narrows MEMBER visibility.

## 2. What data do I need?

| Field | Source | Rules |
|---|---|---|
| project_id | path | must be in actor's org |
| user_id | body | must be an org member; not already a project member |

Entity: `project_members` (project_id, user_id, unique pair) — already defined in `03-database.md`.

## 3. What API endpoint do I need?

| Method | Path | Min role | Success |
|---|---|---|---|
| GET | `/api/v1/projects/{id}/members` | MEMBER (visible project) | 200 list |
| POST | `/api/v1/projects/{id}/members` | MANAGER+ (or creator-MANAGER) | 201 |
| DELETE | `/api/v1/projects/{id}/members/{user_id}` | MANAGER+ | 204 |

## 4. What should the database do?

- INSERT/DELETE on `project_members`; rely on `UNIQUE (project_id, user_id)` for duplicates.
- Visibility queries change shape:
  - MANAGER+: all org projects (unchanged).
  - MEMBER: only projects where a `project_members` row exists for them.
- Removing the last member does NOT block (MANAGER+ still sees the project).

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Project foreign/nonexistent | 404 | `NOT_FOUND` |
| Target not an org member | 422 | `USER_NOT_ORG_MEMBER` |
| Already a project member | 409 | `ALREADY_PROJECT_MEMBER` |
| MEMBER tries to manage members | 403 | `FORBIDDEN` |
| Target is a MEMBER without access listing projects | implicit — list shows nothing | — |

## 6. Who is allowed to perform this operation?

- Manage members: MANAGER+ (creator-MANAGER included).
- View list / see project: current visibility rules.
- Adding requires target to ALREADY be an org member (org membership is the source of truth).

## 7. How do I test it?

1. OWNER adds org member to project → 201; duplicate → 409.
2. Add non-org-member → 422 `USER_NOT_ORG_MEMBER`.
3. MEMBER of project lists tasks → 200; MEMBER *not* on project → project detail/list hide it (404 on direct read).
4. MANAGER+ still lists all org projects regardless of membership rows.
5. Remove member → 204; that MEMBER loses visibility immediately.
6. MEMBER attempts add/remove → 403.
