---
id: WF-2
title: AI V4 Risk Detection — risk ruleset & LLM narration scope
type: grilling
labels: [wayfinder:grilling]
assignee: opencode
status: closed
blocking: []
blocked_by: []
asset: null
---

## Question

What should **Risk Detection** (AI V4) actually compute, and what is the LLM's job?

Sub-questions:

1. **Risk signal set** — which concrete risks do we detect from repository data?
   Candidates: overdue tasks, single-owner / bus-factor projects, unbalanced workload
   across members, stalled (open & untouched > N days) tasks, scope creep (tasks
   growing after due date), overdue-without-assignee, dependency/blocking pile-ups.
   Which make the cut for V4?
2. **Where rules live** — compute signals as repository aggregates (like the V2
   summary stats) or introduce a dedicated `rules` module? How do we keep them
   testable and DB-grounded (no LLM inference in the numbers)?
3. **LLM scope** — does the LLM *only narrate* impact + recommendations from
   DB-computed risk flags (V2-style), or also *rank/prioritize* risks? Keep it
   narration-only to stay grounded?
4. **Output shape** — list of `{risk, severity, evidence, recommendation}` returned
   alongside raw flags (mirroring V2's `stats` + `prose` split)?
5. **Trigger** — on-demand endpoint, scheduled (beat) scan, or both? (Ties to
   ticket 4 backgrounding and ticket 5 beat jobs.)

Outcome: the agreed risk-signal catalog, the computation boundary, and the
LLM's strictly-narration role.

## Resolution

Decided design for **AI V4 Risk Detection**.

- **Risk signal set (core five):** overdue tasks, single-owner / bus-factor projects,
  unbalanced workload across members, stalled (open & untouched > N days) tasks,
  unassigned high-priority/urgent tasks. **Deferred:** scope creep, dependency/blocking
  pile-ups (heavier to compute reliably).
- **Where rules live:** dedicated `app/ai/risk.py` rules module that calls existing
  repositories for raw data and emits typed `RiskSignal` objects. Keeps risk logic
  testable and separate from `AiService`, mirroring V2's aggregation-vs-narration split.
- **LLM scope:** **narration-only** (V2 pattern). The LLM receives the computed
  `RiskSignal[]` and writes impact + per-signal recommendation prose; it does **not**
  invent risks or set severity.
- **Output shape:** `{ risks: [{kind, severity, evidence, recommendation}], narrative: str }`
  — exact computed list + LLM prose. No grounding drift because the LLM only sees
  computed signals.
- **Trigger:** on-demand `GET /ai/projects/{id}/risks` (membership-gated, like V2
  summary). Scheduled/beat scanning deferred to WF-4 (backgrounding) + WF-5 (beat jobs).
- **Severity:** deterministic thresholds in the rules module (LLM never sets it).
  `Severity` enum `low|medium|high`.
- **RiskSignal:** `{kind: str, severity: Severity, evidence: dict}` (evidence holds raw
  numbers/refs, e.g. counts, member names).
- **Errors:** reuse `AiNotConfiguredError`→503 (after membership guard),
  `AiUpstreamError`→502, 404 for non-members, 422 for bad input. No new error types.
- **Config:** no new `Settings`; threshold constants (`STALLED_DAYS`, `OVERDUE_HIGH_DAYS`,
  `WORKLOAD_RATIO`) live in `app/ai/risk.py`.
- **Code:** `app/ai/risk.py` (rules), `AiService.risk_assessment(organization_id,
  project_id)` calls the rules module then `_narrative_call` for per-signal
  recommendations + narrative; endpoint `GET /ai/projects/{id}/risks` in
  `app/api/v1/endpoints/ai.py` wired with `get_current_membership` +
  `get_generating_service`.
