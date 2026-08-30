# Agent Memory for TeamFlow — Research Brief

> Status: research only. No code, migrations, or endpoints were written for this
> brief. It consolidates the 2026 literature on agent memory and proposes a
> phased design for TeamFlow. Implementation is deferred to a later decision.

## 1. Problem

The TeamFlow AI agent (chat V3, risk V4, plan/execute V5) is **stateless**: every
request re-derives context from the live database and discards everything
afterwards. It cannot learn a team's *conventions, preferences and feedback*
("put standup notes in comments, not descriptions"; "Alice wants URGENT work
assigned to Bob"). Without memory the agent repeats mistakes and ignores
hard-won team knowledge.

The research question: **what memory architecture fits an agent that already
sits on a structured, tenant-scoped PostgreSQL database?**

## 2. Method

Surveyed the 2026 agent-memory literature: the field survey (arxiv 2603.07670),
production vendor write-ups (Mem0, Zep/Graphiti, Letta), a storage benchmark
(agentnative, pgvector vs Qdrant), GitHub Copilot's empirical memory study
(davidamitchell, 2026-07), and the "five questions every memory must answer"
framework (stanleycyang, 2026-07). Cross-checked against how coding agents
(OpenCode, Codex, Claude Code) persist memory (static `AGENTS.md`/`CLAUDE.md`
files + compaction).

## 3. Findings

### 3.1 Memory is a *write → manage → read* loop, not a store
The survey formalizes memory as a loop coupled to action across three axes:
temporal scope, representational substrate, and **control policy** (who decides
what to remember). The production lesson across every source: the value is not
*what you store* — it is the **consolidation and forgetting** around it. A raw
transcript dump is the anti-pattern.

### 3.2 What the leading systems teach — and a rule that applies to us
- **Mem0** (vector + graph + KV, auto-extraction): ~90% token reduction vs
  full-context, sub-200ms retrieval, write-time conflict resolution. Best for
  interactive/production.
- **Zep/Graphiti** (temporal knowledge graph): tracks *when* facts changed;
  LongMemEval 63.8% vs Mem0's 49% — but high ingestion latency (retrieval can
  fail for hours post-write). Best for **entity-heavy, evolving relationships**
  (CRM, project trackers).
- **Letta** (OS-metaphor, agent-managed blocks, git-backed): best for
  long-running/audit/compliance where memory transparency matters.

**The decisive rule** (agentmarketcap, 2026-04-08): *"Skip dedicated memory
entirely when the agent has access to a structured database it can query
directly… or where the memory is better modeled as a database query than
semantic retrieval."* **TeamFlow's agent already runs on a structured DB**
(tasks/comments/activity). So most of its "memory" is just SQL — add semantic
memory **only for the small, high-value layer** (conventions, preferences,
feedback), not by vectorizing everything.

### 3.3 Storage: if we go semantic, **pgvector**, not Qdrant
For a Postgres multi-tenant SaaS, pgvector is the clear default (agentnative,
2026-03-13): **11.4× throughput** (471 vs 41 QPS) at 99% recall, tenant
isolation via a `WHERE` clause, single backup/monitoring. Qdrant only wins on
p99 tail latency, native hybrid sparse+dense, and per-subagent isolation — none
of which TeamFlow needs yet. Start with exact + lexical (`tsvector`/GIN), add
HNSW/embeddings only after measuring. Fuse with RRF. **No separate vector
service.**

### 3.4 Production-safety patterns (the part demos get wrong)
- **GitHub Copilot's empirical result** (2026-07): memories store **explicit
  citations + rationale, re-verified against live state before acting**;
  adversarial false-citation memories were consistently self-corrected.
  Measured gain: **+7pp PR merge rate, +2pp positive review** (p<0.00001). This
  validates **reflection grounding** (cite evidence) + **re-verify before act**.
- **The five questions every memory row must answer** (stanleycyang, 2026-07):
  *who owns it, where it came from, whether trustworthy, when it expires, how
  corrected/deleted.* → provenance, trust level, TTL, tenant scope, correction
  path.
- **Poisoning/governance** (zylos, arxiv 2603.07670): external-derived memories
  get lower trust; write-time provenance; periodic audits; selective forgetting.
  Since TeamFlow's memories come from *user/approved actions* (not scraped web),
  risk is low — but the write-gate still matters.

### 3.5 Consolidation = the `/reflect` → `CLAUDE.md` pattern, applied to our DB
Emerging standard (spikelab gist, AWS AgentCore): an **episodic event log**
(flight-recorder: what/when/which tool) captured by default, then a
**consolidation job** distills episodes → semantic memories. For us:
- **Episodic store already exists** → the activity log (records approved agent
  actions with actor/time).
- **Consolidation** (Docker era, Celery beat): distill recent episodes →
  `agent_memory` rows; conflict resolution = newer wins; TTL decay on episodic.
- This is literally Claude Code's `/diary` + `/reflect` → `CLAUDE.md`, stored in
  Postgres.

## 4. Refined TeamFlow design (proposed, not built)

**Phase 1 — now, testable, no vectors:**
- `agent_memory` table: `org_id, project_id, type(convention|preference|feedback),
  scope, content, source(user_explicit|approved_action), trust_level,
  created_by, expires_at, metadata JSONB` — i.e. the "five questions" schema.
- **Read:** retrieve by type/scope/recency, inject as labeled `[MEMORY —
  convention]` blocks with citations (created_by, date) into `propose`/`chat`
  prompts.
- **Write-gate:** only `user_explicit` (a "remember this" command) and
  `approved_action` (e.g. a rejected assign → feedback row). **No free-form
  model reflections** — that's the poisoning/trust boundary.
- Episodic layer = existing activity log (no new table needed).

**Phase 2 — Docker era:** Celery-beat consolidation (episodes → semantic
memories, conflict/decay) + *optional* pgvector column + `tsvector` for hybrid
RRF retrieval, all tenant-scoped. No Qdrant.

## 5. Design gap found during research

While reviewing the V5 `propose` path, the research surfaced that the propose
prompt sends only `allowed_tools` + the instruction — **no project context**, so
the agent cannot reference existing tasks by id ("comment on the onboarding
task" is impossible without task ids). Recommended fix (still research, not
implemented): inject a read-only task snapshot (id + title + status) into the
propose prompt. This is independent of the memory work and should be evaluated
on its own.

## 6. Open questions / further research

- **Eval:** how do we measure memory quality? Copilot used merge-rate; TeamFlow
  would need a task-specific proxy (e.g. agent re-proposes fewer rejected
  actions after feedback).
- **Conflict resolution:** when a new `user_explicit` memory contradicts an old
  one, do we supersede, keep both, or ask? Needs a policy decision.
- **Retrieval scaling:** at what memory volume does keyword/`tsvector` stop
  being enough and pgvector become worth the operational cost? Benchmark before
  adding vectors.
- **Tenant isolation of semantic recall:** confirm pgvector + row-level `WHERE`
  is sufficient at scale, or whether per-org indexes are needed.

## 7. Sources (2026)

- arxiv 2603.07670 — agent memory survey (write/manage/read loop; poisoning)
- Mem0 blog — vector+graph+KV, ~90% token reduction, write-time conflict resolution
- Zep/Graphiti (agentnative benchmark) — temporal graph, LongMemEval 63.8%
- atlan.com (2026-04) — SQL memory 84.2% vs RAG 80.0% LongMemEval, ~10× cheaper
- agentnative (2026-03-13) — pgvector 11.4× throughput vs Qdrant at 99% recall
- davidamitchell (2026-07-20) — Copilot memory: +7pp merge, +2pp review; cite + re-verify
- stanleycyang (2026-07-18) — five questions every memory row must answer
- spikelab gist / AWS AgentCore — episodic log + consolidation job pattern
- OpenCode / Codex / Claude Code docs — `AGENTS.md`/`CLAUDE.md` + compaction as durable memory
