"""The Ollama client against a fake server: valid, malformed and unreachable responses."""

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from agentflow.agents import ollama as ollama_mod
from agentflow.agents.ollama import OllamaClient
from agentflow.models import AgentError


class Answer(BaseModel):
    capabilities: list[str]


REAL_CLIENT = httpx.AsyncClient


def fake_server(monkeypatch, handler):
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", factory)


def chat_reply(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"message": {"content": content}, "prompt_eval_count": 120, "eval_count": 30}
    )


def test_schema_constrained_answer_is_parsed_and_counted(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return chat_reply('<think>hmm</think>```json\n{"capabilities": ["expiry"]}\n```')

    fake_server(monkeypatch, handler)
    parsed, tokens = asyncio.run(OllamaClient(model="m").structured("sys", "prompt", Answer))
    assert parsed.capabilities == ["expiry"] and tokens == 150
    assert seen["format"]["properties"]["capabilities"]["type"] == "array"  # schema sent to Ollama
    assert seen["stream"] is False and seen["model"] == "m"


def test_malformed_output_raises_agent_error(monkeypatch):
    fake_server(monkeypatch, lambda request: chat_reply("Sure! Here are the capabilities: expiry"))
    with pytest.raises(AgentError, match="does not match the schema"):
        asyncio.run(OllamaClient(model="m").structured("sys", "prompt", Answer))


def test_unreachable_server_and_missing_model(monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused")

    fake_server(monkeypatch, down)
    with pytest.raises(AgentError, match="not reachable"):
        asyncio.run(OllamaClient(model="m").check())

    fake_server(monkeypatch, lambda request: httpx.Response(200, json={"models": [{"name": "other:7b"}]}))
    with pytest.raises(AgentError, match="ollama pull m"):
        asyncio.run(OllamaClient(model="m").check())
