# Feature: Token Refresh & Logout

## 1. What problem am I solving?

Access tokens expire after 15 minutes (by design — small blast radius). Without a
refresh mechanism users would re-enter passwords constantly; without *revocable*
refresh tokens, logout and stolen-token response would be impossible.

## 2. What data do I need?

Refresh: the opaque `refresh_token` string. Logout: same, plus a valid access token.
Entities: `refresh_tokens` (read + write), `users` (read, to confirm still active).

## 3. What API endpoint do I need?

### `POST /api/v1/auth/refresh`

Request: `{ "refresh_token": "<opaque>" }`
Success — `200 OK`: **new** access + refresh pair.

### `POST /api/v1/auth/logout` (authenticated)

Request: `{ "refresh_token": "<opaque>" }`
Success — `204 No Content`.

## 4. What should the database do?

Refresh:
1. Hash incoming token → `SELECT refresh_tokens BY token_hash`.
2. Reject if missing / `expires_at < now()` / `revoked_at IS NOT NULL`.
3. **Rotation:** `UPDATE row SET revoked_at = now()`; INSERT new row for the new token.
4. **Reuse detection:** if the presented token was *already* revoked → this is a replay.
   Revoke ALL refresh rows for that user (theft response), return 401.

Logout: set `revoked_at` on the matching, non-revoked row (idempotent — revoking an
already-revoked token still returns 204).

## 5. What can go wrong?

| Failure | Status | Code | Extra action |
|---|---|---|---|
| Token unknown/expired | 401 | `INVALID_REFRESH_TOKEN` | — |
| Token already rotated/revoked (replay) | 401 | `INVALID_REFRESH_TOKEN` | revoke all user's refresh tokens |
| User deactivated since login | 401 | `INVALID_REFRESH_TOKEN` | also revoke all |
| Access token invalid on logout | 401 | `NOT_AUTHENTICATED` | — |

## 6. Who is allowed to perform this operation?

- Refresh: possession of a valid refresh token *is* the authorization (public route, token required).
- Logout: valid access token + the refresh token being revoked.

## 7. How do I test it?

1. Refresh with valid token → 200, NEW pair differs from old, old row `revoked_at` set in DB.
2. Old (rotated) token used again → 401 **and every refresh row of that user is revoked**.
3. Expired refresh token → 401, user's other tokens untouched.
4. Garbage token string → 401, no crash.
5. Refresh works right after rotation within the same second (no clock issues).
6. Logout with valid tokens → 204; subsequent refresh attempt with it → 401.
7. Deactivated user attempts refresh → 401 + all their tokens revoked.
