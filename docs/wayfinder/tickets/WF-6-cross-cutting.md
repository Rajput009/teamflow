---
id: WF-6
title: Cross-cutting — rate limiting, CORS, notification dedupe/outbox
type: grilling
labels: [wayfinder:grilling]
assignee: opencode
status: closed
blocking: []
blocked_by: []
asset: null
---

## Question

Scope and approach for three deferred cross-cutting concerns. Decide whether each is
in this decisions map's scope or pushed to the hand-off effort, and pick the approach
so the hand-off implements consistently.

1. **Rate limiting** — which routes (AI endpoints especially, given provider cost/
   latency)? Strategy: per-user token bucket, fixed window? Middleware vs. reverse
   proxy (nginx, ticket 5)? Where does the limit live?
2. **CORS** — allowed-origins policy for the API. Open (`:*`) for a portfolio demo,
   or a configured allowlist via `app/core/config.py`?
3. **Notification dedupe / outbox** — the notification pipeline (V3 of core) deferred
   dedupe and an outbox. Is that a V5 concern, or does it get its own decision here?
   Approach if in scope: dedupe key, outbox table + relay.

Outcome: a scoped decision per concern (in-scope-with-approach, or explicitly pushed
to hand-off) so nothing is silently dropped.

## Resolution

Decided **scope & approach** for the three deferred cross-cutting concerns.

- **Rate limiting:** **per-user token-bucket on the AI endpoints** (chat / risks / agent)
  via a Redis counter (Redis already in the stack). Behind a `Settings` flag +
  `AI_RATE_LIMIT_PER_MINUTE` constant, default-on for AI routes. General API limiting can
  follow the same pattern later.
- **CORS:** **explicit allowlist via `Settings` (`CORS_ORIGINS`)**, defaulting to empty
  (same-origin only). Never `*` with credentials (JWT requires explicit origins).
  Simple `CORSMiddleware` config.
- **Notification dedupe / outbox:** **dedupe now, outbox deferred** — add an **idempotency
  key** on notification dispatch (same event isn't delivered twice, e.g. quick
  reassign). The full outbox/relay pattern is deferred to a later effort.
- **Scope:** these three are the only cross-cutting items from the original roadmap; none
  overlap the V1–V5 designs already locked. All are decisions, not execution, so they
  belong in this map.
