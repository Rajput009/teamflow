# Authentication

## Overview

- **Access token:** stateless JWT, short-lived (15 min). Sent as `Authorization: Bearer <token>`. No DB lookup per request — that's the point of JWT.
- **Refresh token:** opaque random string, **stored hashed in the database**, long-lived (14 days), revocable. Used only against `POST /auth/refresh`.
- **Password hashing:** Argon2 (fallback bcrypt) via passlib. Never store or log plaintext.

## Token Design

### Access token (JWT)

Claims:

```json
{
  "sub": "3f9c...user-uuid",       // user id
  "exp": 1755888000,                // expiry — now + 15 min
  "iat": 1755887100,                // issued at
  "type": "access",
  "org_id": null                    // active org context (V2+, set at login/refresh)
}
```

- Signed with `HS256` + `jwt_secret_key` from settings.
- Stateless verification: signature + `exp` + `type == "access"`.
- Deliberately minimal: no email, no roles inside the JWT (roles change; tokens can't be edited — read them fresh from the DB via membership).

### Refresh token

```text
raw_token   = secrets.token_urlsafe(48)     # returned to client ONCE
token_hash  = sha256(raw_token)             # stored in refresh_tokens.token_hash
```

Why opaque instead of a second JWT?

| | Opaque refresh | JWT refresh |
|---|---|---|
| Revocation before expiry | ✅ delete/mark row | ❌ must trust until exp |
| Logout everywhere | ✅ revoke all rows for user | ❌ needs a denylist anyway |
| Rotation tracking | ✅ natural (DB rows) | ❌ awkward |

If you need a DB to make a JWT revocable, use the simpler token: an opaque one.

## Flows

### Register

```text
POST /auth/register {email, password, full_name}
  → validate (Pydantic: email format, password policy)
  → check email uniqueness            → 409 if taken
  → hash password (Argon2)
  → INSERT users
  → issue access + refresh tokens      (log the user straight in)
  → 201 {user, tokens}
```

### Login

```text
POST /auth/login {email, password}
  → find user by email                 → 401 if not found*
  → verify password                    → 401 if wrong*
  → if is_active == false              → 403 ACCOUNT_DISABLED
  → generate access + refresh pair (store refresh hash in DB)
  → 200 {tokens}

* Same error for both — never reveal whether an email exists.
```

### Refresh (+ rotation)

```text
POST /auth/refresh {refresh_token}
  → hash incoming token, look up refresh_tokens row
  → not found / expired / revoked      → 401 INVALID_REFRESH_TOKEN
  → ROTATION: mark old row revoked_at = now()
             INSERT new refresh row, return new pair
  → 200 {tokens}
```

Rotation limits the blast radius of a stolen refresh token: it dies on first use.

**Reuse detection:** if a token arrives that is *already revoked*, treat it as evidence of theft → revoke **all** refresh tokens for that user. Cheap and impressive to explain in interviews.

### Logout

```text
POST /auth/logout {refresh_token}    (requires valid access token too)
  → mark that refresh row revoked     → 204
```

The access token stays valid until it expires (~15 min) — acceptable tradeoff; documented, not hidden.

### Protected request

```text
GET /projects
Authorization: Bearer <jwt>
  → decode + verify signature/exp/type
  → load user by sub        → 401 if missing or is_active == false
  → proceed to endpoint logic
```

## Password Policy & Storage

- Minimum 8 chars; checked with Pydantic validator. (No arbitrary complexity rules — NIST 800-63B favors length.)
- Hash with Argon2id (`passlib[argon2]`); verify with constant-time compare built into passlib.
- Hashing happens in `core/security.py`, called by services — never inline in routers.

## Error Semantics

| Situation | Response |
|---|---|
| Missing/malformed Authorization header | 401 `NOT_AUTHENTICATED` |
| Expired/invalid signature/wrong-type JWT | 401 `INVALID_TOKEN` |
| Bad credentials at login | 401 `INVALID_CREDENTIALS` |
| Valid token, deactivated user | 403 `ACCOUNT_DISABLED` |
| Bad/revoked/expired refresh token | 401 `INVALID_REFRESH_TOKEN` |

## Security Checklist

- [ ] Secrets only from environment (`jwt_secret_key`, DB URL).
- [ ] Constant response times/messages for login failures.
- [ ] Refresh tokens stored hashed; raw value exists only in the response body once.
- [ ] Rotation + reuse detection implemented and tested.
- [ ] No tokens in URLs, query strings, or logs.
- [ ] HTTPS enforced in deployment (NGINX, V4).

## Test Matrix (feeds `08-testing-strategy.md`)

1. Register happy path → 201, tokens returned, password stored as hash.
2. Register duplicate email → 409.
3. Register invalid email / short password → 422.
4. Login correct credentials → 200 + both tokens.
5. Login wrong password → 401; unknown email → identical 401 shape.
6. Access protected route without token → 401.
7. Access with expired JWT → 401.
8. Access with tampered signature → 401.
9. Refresh with valid token → new pair, old revoked in DB.
10. Reuse of rotated refresh token → 401 AND all user's refresh tokens revoked.
11. Logout → refresh token no longer usable.
12. Deactivated user's access token → 401 on next request (is_active checked).
