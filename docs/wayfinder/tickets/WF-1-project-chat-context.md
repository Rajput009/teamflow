---
id: WF-1
title: AI V3 Project Chat — context assembly & grounding
type: grilling
labels: [wayfinder:grilling]
assignee: opencode
status: closed
blocking: []
blocked_by: []
asset: null
---

## Question

How should the **Project Chat** endpoint (AI V3) assemble context from
repositories and keep answers grounded in real data?

Sub-questions this decision must resolve:

1. **Context sources** — which repository queries feed the prompt? (project +
   members, tasks with assignment/status, recent activity log, comments?) How much
   history is in scope for a single question?
2. **Context bounding** — how do we cap tokens / prevent sprawling context as a
   project grows? Windowing, top-N, aggregation-only (like V2)?
3. **Endpoint shape** — single `POST /ai/projects/{id}/chat` carrying the question
   plus prior messages (chat memory), or stateless per-call? Where does conversation
   history live (DB, client, nowhere)?
4. **Streaming vs non-streaming** — do we stream the LLM response, or return it
   whole? (Depends on provider streaming support — see ticket 7.)
5. **Grounding & citations** — how do we keep the LLM from hallucinating project
   facts, and how do we let it cite the DB rows it reasoned from? Reuse the V2
   "narrate DB-computed stats only" pattern?
6. **Human-in-the-loop** — is chat read-only (answer questions, never mutate),
   consistent with "AI never writes to DB directly"? Or does it propose actions the
   human approves (bridging toward V5)?

Outcome: a written design the hand-off effort implements against (endpoint contract,
context-assembly module boundary, grounding rules).

## Resolution

Decided design for **AI V3 Project Chat** — a read-only, grounded Q&A surface.

- **Read-only:** chat never mutates state; proposing actions deferred to the V5 agent.
  Preserves "AI never writes to DB directly."
- **Context sources:** project + members, tasks (status/assignee/due/priority), recent
  activity (last ~20), recent comments (last ~20) — all scoped to `organization_id`
  (tenancy isolation).
- **Bounding:** recency window + hard token cap; for large projects fall back to
  aggregation summaries (reuse `TaskRepository` V2 aggregates) rather than enumerating
  all rows.
- **Stateless:** `POST /ai/projects/{id}/chat` with `{question, history?}`; client holds
  history. Persisted `chat_sessions`/`chat_messages` tables deferred (they'd store the
  user's own words via a normal service, not AI state — allowed but out of V3 scope).
- **Transport:** **non-streaming first** (returns JSON); SSE streaming is a later thin
  enhancement over the same `chat()` result. Chosen for robust 503/502 handling and full
  test coverage (`ScriptableLLM` returns the full string, no real stream).
- **Grounding:** narration-only (V2 pattern) — LLM reasons only over the provided context
  blob and says "I don't know" when the answer isn't present. No tool access in V3.
- **Schema:** request `{question: str, history?: [{role, content}]}`; response
  `{answer: str, model: str}`. **Citations dropped from V3** (reliable extraction needs
  `json_schema` structured output; deferred with RAG).
- **History:** cap last 10 messages + token sub-budget; truncate oldest if over.
- **Auth:** requires active project membership via `get_current_membership`; non-members
  get 404 (shared-DB isolation).
- **Not-in-context:** returned as ordinary prose, no error status. Grounding is a prompt
  constraint, not an exception.
- **Activity log:** none (read-only; no state change).
- **Code:** `AiService.chat(organization_id, project_id, question, history)` in
  `app/ai/service.py` reusing `_narrative_call`; a private context-assembly helper
  queries the existing repos. Endpoint in `app/api/v1/endpoints/ai.py` wired with
  `get_current_membership` + `get_generating_service` (needs the LLM). `StreamingResponse`
  added later.
- **Errors:** reuse `AiNotConfiguredError`→503 (fires after the membership guard,
  preserving `UnconfiguredLLMClient` laziness), `AiUpstreamError`→502, body
  validation→422. No new error types.
- **Config:** no new `Settings`; `AI_CHAT_MAX_HISTORY_MESSAGES = 10` and the chat
  system-prompt as constants in `app/ai/prompts.py`.
- **Deferred:** RAG retrieval (embed tasks/comments, top-k by relevance) as a future
  quality upgrade — not in V3.

This is the decision record the hand-off effort implements via TDD at the agreed seams.
