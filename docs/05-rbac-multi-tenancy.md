# RBAC & Multi-Tenancy

These are two separate security layers. Both must pass for any operation:

```text
Layer 1 — Tenancy:   "Is this entity even in YOUR organization?"
Layer 2 — Role:      "Does your role in that org allow this action?"
```

A request failing either layer gets **404 or 403** (rules below), never partial data.

## Roles

```text
OWNER  ── owns the organization (the founder; there is always ≥1)
ADMIN  ── delegated operator
MANAGER ── runs projects and teams
MEMBER ── individual contributor
```

Roles live on the `memberships` row (`org_role` enum) — a user's role is **per-organization**, not global.

## Permission Matrix

Legend: ✅ allowed · ➖ allowed only for own records · ❌ forbidden

| Action | OWNER | ADMIN | MANAGER | MEMBER |
|---|:-:|:-:|:-:|:-:|
| Delete organization | ✅ | ❌ | ❌ | ❌ |
| Update org settings | ✅ | ✅ | ❌ | ❌ |
| View org member list | ✅ | ✅ | ✅ | ✅ |
| Add member to org | ✅ | ✅ | ❌ | ❌ |
| Remove member / change role | ✅ | ✅ | ❌ | ❌ |
| Create project | ✅ | ✅ | ✅ | ❌ |
| Update / archive project | ✅ | ✅ | ➖ creator only | ❌ |
| Delete project | ✅ | ✅ | ➖ creator only | ❌ |
| Add/remove project members | ✅ | ✅ | ➖ projects they manage | ❌ |
| View all org projects | ✅ | ✅ | ✅ | ➖ only projects they're a member of |
| Create task in project | ✅ | ✅ | ✅ | ➖ projects they're a member of |
| Assign task to someone | ✅ | ✅ | ✅ | ❌ |
| Update task fields (status, priority...) | ✅ | ✅ | ✅ | ➖ tasks assigned to them |
| Comment on accessible task | ✅ | ✅ | ✅ | ➖ member of its project |
| View activity log | ✅ | ✅ | ✅ | ➖ activity of visible projects |

Special rules:
1. **Last owner protection:** the final OWNER cannot be demoted, removed, or leave.
2. ADMIN cannot act on (demote/remove) an OWNER.
3. MEMBER self-service: update status of own assigned tasks, comment, edit/delete own comments.

## Multi-Tenancy Model

**Shared database, shared schema, `organization_id` scoping.**

Chosen over schema-per-tenant / DB-per-tenant because it's simpler to operate and
migrate at this scale, and it forces us to get scoping discipline right — which is
the transferable skill.

### The golden rule

> Every query touching tenanted data filters by `organization_id` derived from the
> authenticated user's membership — **never** from client input.

```python
# ❌ WRONG — attacker controls path param, can read other orgs' data
project = await repo.get_by_id(project_id)

# ✅ RIGHT — id AND tenant in one query
project = await repo.get_in_org(project_id, current_org_id)
```

### Enforcement points

```text
get_current_user        → who are you?            (JWT)            → 401 if bad
get_current_membership  → your role in THIS org   (memberships DB) → 403/404 if none
require_permission(...) → role allows action?     (matrix above)   → 403 if not
service layer           → re-checks ownership     ("➖" cases)      → 403 if not
```

FastAPI dependency chain makes layers 1–3 automatic per route; layer 4 is explicit
in services because "own record only" depends on the specific entity.

The `org_id` used everywhere comes from the membership lookup against the JWT's
active-org claim + a DB check that the membership still exists (a removed member's
stale token must stop working immediately).

### Error codes: leak nothing

| Situation | Response | Why |
|---|---|---|
| Entity exists but belongs to another org | **404** `NOT_FOUND` | A 403 would confirm its existence to an attacker |
| Entity in your org, insufficient role | **403** `FORBIDDEN` | You're allowed to know it exists |

## Test Checklist (security tests are first-class citizens)

1. User from Org B requests Org A's project by id → 404.
2. User from Org B lists projects → sees zero of Org A's.
3. MEMBER attempts `DELETE /organization` → 403.
4. MEMBER creates task in a project they're not a member of → 403.
5. MEMBER updates status of their own assigned task → 200.
6. MANAGER assigns a task → 200; MEMBER assigns → 403.
7. Demoting the last OWNER → 409 `LAST_OWNER`.
8. Removed member with cached JWT → 401/404 on all org routes.
9. ADMIN attempts to remove an OWNER → 403.
10. Every scoped repository function receives `organization_id` as a required parameter — enforced by convention and code review.
