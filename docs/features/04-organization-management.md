# Feature: Organization Creation & Members

## 1. What problem am I solving?

A company needs a container for everything: its people, projects, and settings.
Creating an organization is the moment **multi-tenancy begins** — the creator becomes
the first OWNER, and from here on every query must be scoped to this boundary.
Members management (invite/add, roles, removal) is part of this feature because it
shapes the same `memberships` table.

## 2. What data do I need?

Create org: `name` (1–255), optional `description`. `slug` generated from name (uniquified).
Add member: `email` of a registered user + `role` (MANAGER or MEMBER — can't create OWNER/ADMIN directly).
Role change: target user id + new role. Entities: `organizations`, `memberships`, `users`.

## 3. What API endpoint do I need?

| Method | Path | Min role |
|---|---|---|
| POST | `/api/v1/organizations` | authenticated (no org required) |
| GET | `/api/v1/organizations/current` | MEMBER |
| PATCH | `/api/v1/organizations/current` | ADMIN |
| DELETE | `/api/v1/organizations/current` | OWNER |
| GET | `/api/v1/organizations/members` | MEMBER |
| POST | `/api/v1/organizations/members` | ADMIN |
| PATCH | `/api/v1/organizations/members/{user_id}` | ADMIN |
| DELETE | `/api/v1/organizations/members/{user_id}` | ADMIN |

Success examples: `POST /organizations` → 201 with org object; `POST /members` → 201 membership.

## 4. What should the database do?

- Create org: INSERT organization **and** creator's OWNER membership in **one transaction**
  — an org without an owner must be impossible.
- Unique constraints: org `slug`, memberships `(organization_id, user_id)`.
- Role change / removal: UPDATE / DELETE on memberships with the last-owner rule enforced:
  refuse demoting/removing the final OWNER (service check → 409).
- Delete org: cascade removes memberships, projects, tasks, comments, activities.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Add member who doesn't exist / isn't registered | 422 | `USER_NOT_FOUND` |
| User already a member | 409 | `ALREADY_MEMBER` |
| Invalid role in payload | 422 | `VALIDATION_ERROR` |
| Demote/remove last OWNER | 409 | `LAST_OWNER` |
| ADMIN targets an OWNER | 403 | `FORBIDDEN` |
| Duplicate org slug after generation | retried internally; never surfaces | — |
| Non-member calls any `/current` route | 403 | `NO_ORGANIZATION` |

## 6. Who is allowed to perform this operation?

See table above; full matrix in `../05-rbac-multi-tenancy.md`. Key asymmetries:
OWNER > ADMIN (admins cannot touch owners); only OWNER deletes the org;
MANAGERs and MEMBERs are read-only on members list.

## 7. How do I test it?

1. Create org → 201; creator's membership row exists with role OWNER; both rows committed atomically.
2. `GET /current` as member → org data; as outsider → 403.
3. ADMIN adds MANAGER → 201; adding same user twice → 409.
4. Adding unregistered email → 422.
5. MEMBER attempts to add member → 403.
6. Demote sole owner → 409; demote one of two owners → 200.
7. ADMIN removes MEMBER → 204; ADMIN removes OWNER → 403.
8. Removed member's access token no longer grants org routes → 403/404.
9. OWNER deletes org → 204; cascaded rows gone (verify tasks/comments deleted).
