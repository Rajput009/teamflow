# Feature: User Registration

## 1. What problem am I solving?

A company employee needs an account before they can do anything in TeamFlow.
Registration is the front door — it must be welcoming to valid users, hostile to
bad data, and leak nothing (no "this email is taken" oracle beyond what's acceptable).

## 2. What data do I need?

| Field | Type | Rules |
|---|---|---|
| email | string | Valid email format, max 255, lowercased before storage/lookup |
| password | string | Min 8 chars (NIST-style: length over complexity), max 128 |
| full_name | string | 1–255 chars, trimmed |

Entity created: `users` row. No organization yet — org creation is a separate feature.

## 3. What API endpoint do I need?

`POST /api/v1/auth/register`

Request:

```json
{ "email": "ahmed@example.com", "password": "s3cretpass", "full_name": "Ahmed Khan" }
```

Success — `201 Created`:

```json
{
  "user": { "id": "<uuid>", "email": "ahmed@example.com", "full_name": "Ahmed Khan", "created_at": "..." },
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

(Registering logs you straight in — no pointless extra step.)

## 4. What should the database do?

- `INSERT INTO users (...)` with `hashed_password` = Argon2 hash.
- Rely on the **unique constraint on email** as the final guard against races
  (two simultaneous registrations of the same email → one succeeds, one catches
  the integrity error → 409).
- Single transaction; refresh token row inserted atomically with the user.

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Malformed body / invalid email / short password | 422 | `VALIDATION_ERROR` |
| Email already registered | 409 | `EMAIL_ALREADY_EXISTS` |
| DB unreachable / insert fails | 500 | `INTERNAL_ERROR` |

Never echo the submitted password back in any response or log.

## 6. Who is allowed to perform this operation?

Public. No token. Rate limiting added in V5.

## 7. How do I test it?

1. Happy path → 201, response contains user + tokens.
2. Stored password is a hash (starts with `$argon2`), never plaintext.
3. Duplicate email → 409 with documented code.
4. Invalid email format → 422.
5. Password of 7 chars → 422; exactly 8 → 201.
6. Email stored lowercase regardless of input case (`AHMED@...`).
