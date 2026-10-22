"""Ollama client for answer generation only (qwen2.5:3b by default). Uses /api/chat and /api/tags.
Embeddings are NOT produced here any more (see embeddings.py)."""
from __future__ import annotations

import logging

import httpx

from app.core.errors import InvalidLLMResponse, LLMTimeout, LLMUnavailable, ModelNotInstalled
from app.services.providers.llm_base import LLMProvider

log = logging.getLogger(__name__)


def _installed(target: str, names: list[str]) -> bool:
    return any(n == target or (":" not in target and n == f"{target}:latest") for n in names)


class OllamaClient(LLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float, temperature: float,
                 num_ctx: int, max_output_tokens: int, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.temperature, self.num_ctx, self.max_output_tokens = temperature, num_ctx, max_output_tokens
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self):
        await self._http.aclose()

    async def health(self) -> dict:
        """ollama: up|unreachable|error; chat_model: available|missing|unknown."""
        try:
            r = await self._http.get("/api/tags", timeout=5)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
        except (httpx.ConnectError, httpx.TimeoutException):
            return {"ollama": "unreachable", "chat_model": "unknown"}
        except (httpx.HTTPError, ValueError):
            return {"ollama": "error", "chat_model": "unknown"}
        return {"ollama": "up", "chat_model": "available" if _installed(self.model, names) else "missing"}

    async def _post(self, path: str, payload: dict, model: str) -> dict:
        try:
            r = await self._http.post(path, json=payload)
        except httpx.ConnectError:
            raise LLMUnavailable() from None
        except httpx.TimeoutException:
            log.warning("Ollama request to %s timed out", path)
            raise LLMTimeout() from None
        except httpx.HTTPError:
            raise LLMUnavailable() from None
        if r.status_code == 404:  # Ollama: {"error":"model 'x' not found"}
            raise ModelNotInstalled(f"Model '{model}' is not installed. Run: ollama pull {model}")
        if r.status_code >= 400:
            log.error("Ollama returned HTTP %s for %s", r.status_code, path)
            raise LLMUnavailable()
        try:
            return r.json()
        except ValueError:
            raise InvalidLLMResponse() from None

    async def chat(self, messages: list[dict]) -> str:
        # Deliberately no retry: a timed-out generation is expensive and may still be running.
        data = await self._post("/api/chat", {
            "model": self.model, "messages": messages, "stream": False, "format": "json",
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "num_predict": self.max_output_tokens},
        }, self.model)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise InvalidLLMResponse()
        return content
