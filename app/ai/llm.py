"""LLM access layer.

One protocol, any provider: the rest of the application depends only on
LLMClient, so swapping OpenRouter for OpenAI, Groq, or a local Ollama is a
configuration change, not a code change. The first adapter speaks the
OpenAI-compatible chat-completions wire format (which OpenRouter, OpenAI,
Groq, Together and Ollama all expose).
"""
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.exceptions import AiUpstreamError


class LLMClient(Protocol):
    """The ONLY surface the AI services are allowed to know about."""

    async def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text answer. Raises AiUpstreamError on any
        transport or provider failure — callers never see httpx exceptions."""
        ...


class OpenAICompatClient:
    """Minimal async client for OpenAI-compatible /chat/completions endpoints.

    Deliberately hand-rolled on httpx instead of pulling in an SDK: one
    endpoint, one request shape, zero vendor lock — and the retry/timeout
    policy stays ours.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.llm_base_url.rstrip("/")
        self._model = settings.llm_model
        self._timeout = settings.llm_timeout_seconds
        self._api_key = settings.llm_api_key

    async def complete(self, *, system: str, user: str) -> str:
        assert self._api_key is not None  # guaranteed by build_llm_client()
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,  # structured generation wants determinism
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AiUpstreamError(
                message=f"The AI provider rejected the request (HTTP {exc.response.status_code})."
            ) from None
        except httpx.HTTPError:
            raise AiUpstreamError() from None

        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            # 200 OK but an unexpected body shape — still an upstream problem
            raise AiUpstreamError() from None


class UnconfiguredLLMClient:
    """Placeholder used when no API key is set. Dependency injection stays
    uniform (every request gets SOME client); the 503 fires only when a
    generation actually needs the provider — so authorization, tenancy and
    validation errors always take precedence over 'AI is off'."""

    async def complete(self, *, system: str, user: str) -> str:
        from app.core.exceptions import AiNotConfiguredError

        raise AiNotConfiguredError()


def build_llm_client() -> LLMClient:
    """Composition root for the AI layer. Never raises: an unconfigured
    deployment gets a client that fails at use-time with the designed 503."""
    if get_settings().llm_api_key is None:
        return UnconfiguredLLMClient()
    return OpenAICompatClient()
