"""Ollama backend for LLM-mode agents: local models, no API key, nothing leaves the network.

Same contract as the Claude client: structured(system, prompt, schema) returns a validated
Pydantic object and a token count, or raises AgentError (which triggers the node's
retry/fallback path). Ollama's `format` field is given the JSON schema, so decoding is
constrained to the expected shape; the result is still validated before use.
"""

from __future__ import annotations

import json
import os
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..models import AgentError

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "qwen2.5-coder:7b"
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaClient:
    def __init__(
        self, base_url: str | None = None, model: str | None = None, timeout: float | None = None
    ) -> None:
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.timeout = timeout or float(os.environ.get("OLLAMA_TIMEOUT", "900"))

    def describe(self) -> str:
        return f"ollama:{self.model} @ {self.base_url}"

    async def check(self) -> None:
        """Fail fast with a useful message if the server or model is missing."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:  # one client per call: loop-safe
                resp = await client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AgentError(f"Ollama not reachable at {self.base_url}: {exc}") from exc
        names = {m["name"] for m in resp.json().get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            raise AgentError(
                f"model {self.model} not pulled (have {sorted(names)}); run: ollama pull {self.model}"
            )

    async def structured(self, system: str, prompt: str, schema: type[T]) -> tuple[T, int]:
        body = {
            "model": self.model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0.1, "num_ctx": 16384},
            "messages": [
                {"role": "system", "content": system + "\nAnswer with JSON matching the schema only."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=body)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AgentError(f"Ollama timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise AgentError(f"Ollama request failed: {exc}") from exc
        data = resp.json()
        text = _THINK.sub("", data.get("message", {}).get("content", "")).strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AgentError(f"model output does not match the schema: {str(exc)[:200]}") from exc
        tokens = int(data.get("prompt_eval_count", 0)) + int(data.get("eval_count", 0))
        return parsed, tokens
