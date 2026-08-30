# Feature: User Login

## 1. What problem am I solving?

A returning user must prove who they are and receive credentials for subsequent
requests. This is where the token pair (short-lived access JWT + revocable refresh
token) enters the system.

## 2. What data do I need?

| Field | Type | Rules |
|---|---|---|
| email | string | Required |
| password | string | Required |

Entities read: `users`. Entity written: `refresh_tokens`.

## 3. What API endpoint do I need?

`POST /api/v1/auth/login`

Request:

```json
{ "email": "ahmed@example.com", "password": "s3cretpass" }
```

Success — `200 OK`: same token shape as registration.

## 4. What should the database do?

- `SELECT user BY email` (lowercased).
- Verify password against stored hash (passlib, constant-time).
- On success: `INSERT refresh_tokens` row with `sha256(raw_token)` and 14-day expiry.
- No writes to the users table (no last_login in V1 — avoids write amplification).

## 5. What can go wrong?

| Failure | Status | Code |
|---|---|---|
| Unknown email **or** wrong password | 401 | `INVALID_CREDENTIALS` (identical body/message for both) |
| Account deactivated (`is_active=false`) | 403 | `ACCOUNT_DISABLED` |
| Malformed body | 422 | `VALIDATION_ERROR` |

Security rule: unknown email and wrong password are **indistinguishable** from the
outside, so the endpoint can't be used to enumerate registered emails.

## 6. Who is allowed to perform this operation?

Public. Deactivated accounts get an explicit 403 (they *did* authenticate correctly).

## 7. How do I test it?

1. Correct credentials → 200 + both tokens; refresh row exists in DB, stores a hash not the raw token.
2. Wrong password → 401.
3. Unknown email → 401, response byte-for-byte identical shape to wrong-password case.
4. Uppercase email variant logs in fine (case-insensitive lookup).
5. Deactivated user → 403 `ACCOUNT_DISABLED`.
6. Missing fields → 422.
