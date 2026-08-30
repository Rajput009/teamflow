# LLM Capabilities for an OpenRouter-backed FastAPI Backend

**Scope:** What OpenRouter-compatible LLM models support that affects the design of an AI feature set for a FastAPI project-management backend (chat UX, agent tool-calling, structured extraction).

**Base assumption:** The default config already uses OpenRouter as the OpenAI-compatible base URL (`https://openrouter.ai/api/v1`), so the OpenAI Chat Completions shape (`/api/v1/chat/completions`) is the contract we build against. OpenRouter is a proxy/router over hundreds of models; capabilities depend on both the *model* and the *provider endpoint* it is served from.

**Primary sources:**
- OpenRouter docs index: https://openrouter.ai/docs/llms.txt
- Streaming: https://openrouter.ai/docs/api_reference/streaming
- Tool & Function Calling: https://openrouter.ai/docs/guides/features/tool-calling
- Structured Outputs: https://openrouter.ai/docs/guides/features/structured-outputs
- Parameters (`response_format`, `tools`, etc.): https://openrouter.ai/docs/api_reference/parameters
- Response Healing (JSON repair): https://openrouter.ai/docs/guides/features/plugins/response-healing
- Live model catalog + pricing + `supported_parameters`: https://openrouter.ai/api/v1/models (and filter UI https://openrouter.ai/models?supported_parameters=tools / `=structured_outputs`)

---

## 1. Streaming

**Supported: Yes — on every model.** OpenRouter allows streaming from *any* model by setting `"stream": true` in a `/api/v1/chat/completions` request. The protocol is **SSE (Server-Sent Events)** over HTTP, mirroring the OpenAI streaming shape: each event is a `data: {json}` line, with tokens carried in `choices[0].delta.content`, terminated by `data: [DONE]`. (Source: https://openrouter.ai/docs/api_reference/streaming)

**Protocol details for a server proxy:**
- Response is `text/event-stream`. Body lines are `data: <json>`; lines beginning with `:` are SSE comments (e.g. `: OPENROUTER PROCESSING` keep-alives) and must be skipped before `JSON.parse` or the stream loop crashes (Source: streaming doc, "Additional information").
- Mid-stream errors arrive as a normal SSE `data:` event with a top-level `error` field and `choices[0].finish_reason: "error"` (HTTP stays 200 because headers were already sent). Pre-stream errors return a standard JSON error with a proper HTTP status (400/401/402/429/502/503). Your proxy must handle both.
- The generation id is provided in the `X-Generation-Id` response header.
- **Stream cancellation** is supported by aborting the connection, and for supported providers (OpenAI, Anthropic, Together, DeepSeek, etc.) this stops processing and billing immediately. Not all providers support it (e.g. Bedrock, Groq, Google, Mistral do not). If you expose a "stop generating" button, surface this best-effort.

**How a FastAPI backend should stream to the client:** Receive `stream=true` from OpenRouter, then re-emit SSE to the browser (Starlette `StreamingResponse` with `media_type="text/event-stream"`). Recommended: use a spec-compliant SSE parser (`eventsource-parser`) server-side to normalize framing, then forward `choices[0].delta.content` as your own SSE `data:` events; forward or translate `finish_reason`/`error` into a terminal event. Note: streaming is compatible with `response_format: json_schema` (partial valid JSON is streamed) — but you typically want tool-calling and JSON extraction in a non-streaming final pass for reliability.

---

## 2. Function / Tool Calling

**Supported: Yes — native, OpenAI-compatible.** OpenRouter standardizes tool calling across models/providers using the OpenAI `tools` request shape (`type: "function"`, `function: {name, description, parameters: <JSON Schema>}`). The model emits `finish_reason: "tool_calls"` with a `tool_calls` array; the *server* (not the model) executes the tool and returns results as `role: "tool"` messages with `tool_call_id`. (Source: https://openrouter.ai/docs/guides/features/tool-calling)

**Key implementation rules from the docs:**
- The `tools` parameter **must be included on every request** in the conversation (the router validates the schema each call).
- `tool_choice` is supported: `none` / `auto` / `required` / `{"type":"function","function":{"name":...}}` to force a specific tool. `parallel_tool_calls` (default `true`) allows multiple simultaneous calls.
- "Interleaved thinking" (reasoning between tool calls) is available on reasoning-capable models (e.g. `anthropic/claude-sonnet-4.5`) — useful for multi-step PM agents, but increases latency and token cost.
- A simple agentic loop (call → execute tools → re-call until no `tool_calls`) is the expected pattern; this is straightforward to implement in FastAPI with a loop bounded by max steps.

**Reliability / model support:**
- Model support is per-model **and** per-provider-endpoint. Filter the catalog at `https://openrouter.ai/models?supported_parameters=tools`. Most frontier and mid-tier chat models support tools (verified live: `gpt-4o-mini`, `gpt-4o`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `deepseek/deepseek-chat`, `llama-3.1-8b` all report `tools: true`).
- Reliability varies by model tier: smaller/cheaper models emit malformed JSON arguments more often — validate/parse `tool_calls[*].function.arguments` with a tolerant parser and a retry/repair step. Tool-call arg parsing should be wrapped in try/except with a re-prompt fallback.
- OpenRouter also offers **server-side tools** (web search, web fetch, datetime, etc.) and an **Agent SDK** (`@openrouter/agent`, `callModel`) that handles the tool loop for you if you later move logic client-side — but for a FastAPI backend the standard `tools` loop is the right building block.

---

## 3. Structured / JSON Output

**Supported: Yes — two mechanisms.**

**(a) JSON mode (`response_format: {type: "json_object"}`)** — guarantees the message content is *valid JSON* (not necessarily a specific shape). You must still instruct the model to emit JSON via the prompt. (Source: https://openrouter.ai/docs/api_reference/parameters, "Response Format")

**(b) Schema-guided Structured Outputs (`response_format: {type: "json_schema", json_schema: {name, strict, schema}}`)** — enforces a JSON Schema. With `strict: true`, providers with native strict mode guarantee schema-conforming output; others treat it as a strong hint. Works with streaming too. (Source: https://openrouter.ai/docs/guides/features/structured-outputs)

**Important caveats (from docs):**
- Structured outputs are supported by *select* models, and support is determined **per endpoint** (the same model on different providers may or may not support it). Verify per model at `https://openrouter.ai/models?supported_parameters=structured_outputs` and the model's Providers section (`structured_outputs` param).
- To guarantee routing only to endpoints that support it: set `require_parameters: true` in provider preferences and include `response_format`/`json_schema` in required params (see Provider Routing doc).
- For **non-streaming** `json_schema` requests, the **Response Healing** plugin can auto-fix imperfect JSON when models drift — a good safety net for PM-data extraction. (Source: https://openrouter.ai/docs/guides/features/plugins/response-healing)
- For a FastAPI backend, prefer structured outputs for deterministic tasks (e.g. summarizing a task into a JSON status object, extracting entities). Keep a parser/repair fallback regardless of `strict`, since compliance "is not guaranteed on every endpoint."

---

## 4. Model Recommendations (portfolio-project cost/latency profile)

Live pricing (USD per token, from `https://openrouter.ai/api/v1/models`, 2026-08 snapshot) and capability flags:

| Model | In / Out (per 1M tok) | tools | structured | Notes |
|---|---|---|---|---|
| `meta-llama/llama-3.1-8b-instruct` | $0.05 / $0.08 | yes | yes | Cheapest; weakest reasoning/instruction-following |
| `google/gemini-2.5-flash-lite` | $0.10 / $0.40 | yes | yes | Very cheap, fast, capable; best default for cost |
| `openai/gpt-4o-mini` | $0.15 / $0.60 | yes | yes | Reliable, cheap, widely compatible |
| `deepseek/deepseek-chat` | $0.26 / $1.03 | yes | yes | Strong reasoning, low cost |
| `google/gemini-2.5-flash` | $0.30 / $2.50 | yes | yes | Higher quality + long context; good "smart" tier |
| `openai/gpt-4o` | $2.50 / $10.00 | yes | yes | High quality, expensive — use sparingly |

**Suggested defaults per use case:**

- **Chat conversation (casual PM assistant / team chat):** `google/gemini-2.5-flash-lite` (cheapest, supports streaming, tools, and structured out) with `openai/gpt-4o-mini` as the drop-in fallback. These keep per-request cost negligible for a portfolio app while supporting all three capability classes.

- **Factual narration (summaries, status reports, meeting notes):** `openai/gpt-4o-mini` or `google/gemini-2.5-flash` — slightly higher quality and longer context for coherent, accurate prose. Use `gemini-2.5-flash` when longer context (project history) matters.

- **Tool-use / agentic (creating tasks, querying project state, multi-step actions):** `openai/gpt-4o-mini` or `google/gemini-2.5-flash` — most reliable `tool_calls` argument conformance among the cheap tier. Reserve `gpt-4o`/`gemini-2.5-flash` (higher tier) only if you observe weak tool-calling on the mini models for complex schemas. Avoid `llama-3.1-8b` for critical tool use despite its `tools` flag, due to flakier argument formatting.

**Design implication:** Because capabilities are uniform across these models (all support streaming, tools, and json_schema), you can make the model slug a single configurable `settings` value and switch tiers per feature without code changes. Default the whole app to `gemini-2.5-flash-lite` (chat + tools + structured all work), and use a `reasoning`/higher-quality override for narration endpoints. Always wrap tool-arg parsing and JSON extraction in validation + retry/Response-Healing, since strict compliance is not guaranteed on every provider endpoint.

---

*Caveat: Model slugs, pricing, and per-endpoint capability support change frequently on OpenRouter. Re-verify the live catalog (`https://openrouter.ai/api/v1/models`) before locking the default in production config.*
