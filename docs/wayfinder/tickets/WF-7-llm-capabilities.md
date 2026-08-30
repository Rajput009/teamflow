---
id: WF-7
title: Research — LLM provider capabilities (streaming, tool calling, JSON mode)
type: research
labels: [wayfinder:research]
assignee: research-subagent
status: closed
blocking: []
blocked_by: []
asset: null
---

## Question

What do OpenRouter-compatible models actually support that affects our AI design?
This ticket is **research (AFK)** — resolved by a subagent, not by grilling.

Investigate and report on:

1. **Streaming** — do the models we'd use support streaming completions? Affects
   WF-1 (chat UX: stream vs. return-whole) and WF-4 (backgrounding contract).
2. **Function / tool calling** — native tool-calling protocol availability and
   reliability. Affects WF-3 (agent tools: native vs. parsed-JSON proposals).
3. **Structured / JSON output** — JSON mode or schema-guided output. Affects how
   V1/V2/V4 parse validated LLM output (currently a retry-loop parser in
   `AiService._validated_call`).
4. **Provider/model recommendations** — a default `LLM_MODEL` for chat vs.
   narration vs. tool-use, given cost/latency for a portfolio project.

Deliver findings to `docs/wayfinder/research/llm-capabilities.md` so WF-1 and WF-3
can resolve against facts. Network calls happen only in this research subagent; the
main suite stays mocked.

## Resolution

Resolved by research subagent (AFK). Findings written to
`docs/wayfinder/research/llm-capabilities.md`.

**Answer:** OpenRouter's OpenAI-compatible API (`/api/v1/chat/completions`) supports,
uniformly across most models:
1. **Streaming** via SSE (`stream:true`; `choices[].delta.content`, `data:[DONE]`,
   handle `:`-comment keep-alives and mid-stream `error` events).
2. **Native tool/function calling** (OpenAI-shaped `tools` + `tool_calls` loop,
   `tool_choice`, `parallel_tool_calls`), reliability varies by model tier.
3. **Structured output** via `json_object` JSON mode and schema-guided `json_schema`
   (strict on capable endpoints; Response-Healing plugin for repair).

**Model recommendations:** default chat to `google/gemini-2.5-flash-lite` (cheapest,
supports all three); narration/tool-use to `gpt-4o-mini` / `gemini-2.5-flash`. Wrap
all LLM output in validation/retry since strict compliance isn't guaranteed on every
endpoint. This unblocks WF-1 (streaming is viable) and WF-3 (native tool calling is
viable, but parsed-JSON proposals remain a safe fallback).
