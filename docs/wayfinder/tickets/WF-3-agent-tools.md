---
id: WF-3
title: AI V5 Agent — tool whitelist & approval gate
type: grilling
labels: [wayfinder:grilling]
assignee: opencode
status: closed
blocking: []
blocked_by: [WF-1, WF-2]
asset: null
---

## Question

How should the **Agent** (AI V5) expose actions and stay safe?

Blocked by WF-1 (grounding/context patterns) and WF-2 (risk framing) because the
agent reuses those context-assembly and guard patterns.

Sub-questions:

1. **Tool surface** — which existing `app/services/*` methods become agent tools?
   (e.g. create task, assign task, update status, post comment, summarize.) How is
   the whitelist declared and kept in sync with the services?
2. **Approval gate** — does the human confirm *each* tool call before execution
   (explicit accept/reject), or approve a plan up front? Where does the gate live
   (endpoint contract, two-step like V1's draft→accept)?
3. **Authorization** — how do we guarantee the agent cannot perform actions the
   calling user lacks permission for? Reuse `app/services/permissions.py` guards
   inside every tool so the agent is bound by the user's role/tenancy.
4. **Sandboxing** — how are tool executions isolated (transaction, error handling,
   audit trail of agent actions in the activity log)?
5. **Tool-calling mechanism** — native function/tool calling vs. parsed JSON
   proposals validated as untrusted input (consistent with V1's acceptance pattern).
   Depends on ticket 7 provider capabilities.

Outcome: the tool whitelist design, the approval-gate contract, and the
authorization enforcement strategy.

## Resolution

Decided design for **AI V5 Agent** — tools + approval gate.

- **Tool surface:** safe whitelist — `create_task`, `assign_task`, `update_task_status`,
  `add_comment`, plus read-only `summarize_project` (reuses V2). Each tool is a thin
  wrapper over an existing service method; no new business logic. Destructive tools
  (delete project/member) deferred from V5.
- **Approval gate:** **explicit per-action approval (two-step)**, mirroring V1's
  draft→accept. The agent proposes a sequence; the human approves/rejects each (or all)
  before execution. No auto-execution.
- **Authorization:** every tool wrapper calls the existing service method, which enforces
  `app/services/permissions.py` guards. The agent is bound by the **calling user's
  role/tenancy** — it cannot overreach. No separate auth path.
- **Sandboxing & audit:** approved calls execute through normal services, so the
  activity log records agent actions and transactions/optimistic locking apply. A failed
  tool call aborts the plan; errors surface as normal API errors.
- **Mechanism:** **parsed-JSON proposals** validated as untrusted input, then executed
  via services (consistent with V1; provider-agnostic). Native `tool_calls` not trusted.
- **Endpoint/execution:** multi-turn, backgrounded. `POST /ai/projects/{id}/agent`
  `{instruction}` enqueues a Celery job → `{job_id}`; `GET /ai/jobs/{job_id}` polls
  proposed actions; `POST /ai/projects/{id}/agent/approve` submits the approved subset →
  executes. Membership-gated throughout.
- **Whitelist registry:** `app/ai/tools.py` maps tool name → `(service_method,
  required_permission, args_schema)`; explicit registration (not auto-discovered).
- **Proposal schema:** `{tool: str, args: dict}` validated by a per-tool Pydantic schema;
  the returned list is validated as untrusted input; unknown tool or schema failure is
  rejected, never executed.
- **Errors:** reuse `AiNotConfiguredError`→503 (after membership guard),
  `AiUpstreamError`→502, 404 non-members, 422 for bad proposal/args, 422 for a
  non-whitelisted tool. **No new error types.**
- **Propose vs approve:** propose is **backgrounded** (slow LLM reasoning → job_id + poll,
  per WF-4); approve executes **inline** (quick service calls, sync).
