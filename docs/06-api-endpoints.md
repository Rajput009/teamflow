# API Design

## Conventions

- **Base path:** `/api/v1` — versioning from day one.
- **Auth:** `Authorization: Bearer <access_token>` on everything except register/login/refresh.
- **JSON only.** Dates as ISO 8601 (`2026-10-30`). UUIDs as strings.
- **Pagination envelope** for list endpoints:

```json
{
  "items": [ ... ],
  "total": 137,
  "page": 2,
  "limit": 20,
  "pages": 7
}
```

Query params: `?page=1&limit=20` (limit capped at 100, validated by Pydantic).

- **Error envelope** (every non-2xx response):

```json
{
  "error": {
    "code": "PROJECT_NOT_FOUND",
    "message": "Project not found.",
    "details": []
  }
}
```

`details` carries field-level info for validation errors:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request data is invalid.",
    "details": [
      { "field": "password", "issue": "String should have at least 8 characters" }
    ]
  }
}
```

- **Filtering / sorting / search** on task lists:
  `GET /api/v1/projects/{id}/tasks?status=IN_PROGRESS&priority=URGENT&assigned_to=<uuid>&search=auth&ordering=-due_date`
  (`-` prefix = descending; allowed fields whitelisted — never pass raw column names to the ORM.)

## Endpoint Inventory

### V1 — Auth & core entities

| Method | Path | Description | Min role |
|---|---|---|---|
| POST | `/auth/register` | Create account | public |
| POST | `/auth/login` | Get token pair | public |
| POST | `/auth/refresh` | Rotate refresh token | public (token required) |
| POST | `/auth/logout` | Revoke refresh token | authenticated |
| GET | `/auth/me` | Current user profile | authenticated |
| POST | `/organizations` | Create org (creator becomes OWNER) | authenticated |
| GET | `/organizations/current` | Own org details | MEMBER |
| PATCH | `/organizations/current` | Update org settings | ADMIN |
| DELETE | `/organizations/current` | Delete org | OWNER |
| GET | `/organizations/members` | List members | MEMBER |
| POST | `/organizations/members` | Add member | ADMIN |
| PATCH | `/organizations/members/{user_id}` | Change role | ADMIN |
| DELETE | `/organizations/members/{user_id}` | Remove member | ADMIN |
| GET/POST | `/projects` | List own visible / create project | MEMBER* / MANAGER |
| GET/PATCH/DELETE | `/projects/{id}` | Project detail / update / delete | MEMBER* / rules in matrix |
| GET/POST | `/projects/{id}/tasks` | List (filterable) / create tasks | MEMBER* |
| GET/PATCH/DELETE | `/tasks/{id}` | Task detail / update / delete | MEMBER* / rules in matrix |
| POST | `/tasks/{id}/assign` | Assign to a member | MANAGER |

\* `MEMBER` visibility restricted per project-membership rules.

### V2 additions

| Method | Path | Description |
|---|---|---|
| GET/POST/DELETE | `/projects/{id}/members` | Manage project membership |
| GET/POST | `/tasks/{id}/comments` | List / add comments |
| DELETE | `/comments/{id}` | Delete own comment (ADMIN+: any) |
| GET | `/activities` | Org-wide audit trail (filterable, paginated) |
| GET | `/projects/{id}/activities` | Per-project history |

### V3 additions

| Method | Path | Description |
|---|---|---|
| GET | `/notifications` | List own notifications |
| POST | `/notifications/{id}/read` | Mark read |
| POST | `/notifications/read-all` | Mark all read |

## Detailed Contracts (V1)

### `POST /auth/register`

Request:

```json
{ "email": "ahmed@example.com", "password": "s3cretpass", "full_name": "Ahmed Khan" }
```

Responses:

- `201` → `{ "user": { "id", "email", "full_name", "created_at" }, "access_token", "refresh_token", "token_type": "bearer" }`
- `409` `EMAIL_ALREADY_EXISTS`
- `422` `VALIDATION_ERROR`

### `POST /auth/login`

Request: `{ "email", "password" }`

- `200` → same token shape as register
- `401` `INVALID_CREDENTIALS` (identical body for unknown email and wrong password)

### `POST /auth/refresh`

Request: `{ "refresh_token": "<opaque>" }`

- `200` → new token pair (old refresh token revoked)
- `401` `INVALID_REFRESH_TOKEN` (also triggers global revocation on reuse — see auth doc)

### `POST /organizations`

Request: `{ "name": "Acme Software", "description": "..." }`

- `201` → organization object; creator gets an `OWNER` membership in the same transaction
- `422` validation errors

### `POST /projects`

Headers: bearer token. Request:

```json
{ "name": "E-commerce Website", "description": "Build new online store", "deadline": "2026-10-30" }
```

- `201` → `{ id, organization_id, name, description, status: "PLANNING", deadline, created_by_id, created_at }`
- `403` if role < MANAGER · `422` invalid input

### `POST /tasks/{task_id}/assign`

Request: `{ "user_id": "<uuid of an org member>" }`

Checks (in order): task exists **in your org** (else 404) → you are MANAGER+ (else 403) → target is a member of the task's org (else 422 `USER_NOT_ORG_MEMBER`) → assign + activity row → `200` task object.

## OpenAPI

FastAPI auto-generates `/docs` (Swagger UI) and `/openapi.json`. Response models are
declared on every route so the docs stay truthful — this doubles as living API documentation
for the portfolio.
