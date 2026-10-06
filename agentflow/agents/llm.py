"""Claude backend for LLM-mode agents (optional; the default provider is deterministic).

Calls are structured (schema-validated) so model output enters the pipeline as data, not
free text. Token use is reported back to the engine for budget enforcement.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

from pydantic import BaseModel

from ..models import AgentError

T = TypeVar("T", bound=BaseModel)
MODEL = "claude-opus-5-5"


class ClaudeClient:
    def __init__(self, model: str = MODEL, effort: str = "high") -> None:
        import anthropic  # imported lazily: deterministic runs need no SDK or credentials

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def _call(self, system: str, prompt: str, schema: type[T]) -> tuple[T, int]:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                output_format=schema,
                # Server-side fallback: a safety-classifier decline is retried on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIStatusError as exc:
            raise AgentError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise AgentError(f"Claude API unreachable: {exc}") from exc
        if response.stop_reason == "refusal":
            raise AgentError("model declined the request")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise AgentError(f"unusable model output (stop_reason={response.stop_reason})")
        usage = response.usage
        return response.parsed_output, usage.input_tokens + usage.output_tokens

    async def structured(self, system: str, prompt: str, schema: type[T]) -> tuple[T, int]:
        return await asyncio.to_thread(self._call, system, prompt, schema)
