"""Thin LLM adapters (no SDK needed) behind one neutral message format.

Neutral messages used by the agent loop:
  {"role": "user", "text": str}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "results": [{"id", "name", "content": str}]}

Providers:
  * AnthropicClient   - Anthropic Messages API (ANTHROPIC_API_KEY)
  * OpenAICompatClient - any OpenAI-compatible /chat/completions endpoint
                         (OpenAI, Groq, OpenRouter, Gemini's compat endpoint, a local server...)
"""
from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings, get_settings
from .http_cache import http_post_json


class LLMError(RuntimeError):
    pass


@dataclass
class LLMTurn:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                 max_tokens: int = 1200) -> LLMTurn: ...


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    try:
        return http_post_json(url, body, headers)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
        raise LLMError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc


class AnthropicClient:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com"):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.name = f"anthropic:{model}"

    @staticmethod
    def _convert(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
                out.append({"role": "assistant", "content": blocks or m.get("text", "")})
            else:  # tool results
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
        return out

    def complete(self, system, messages, tools, max_tokens=1200) -> LLMTurn:
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "system": system,
                                "messages": self._convert(messages)}
        if tools:
            body["tools"] = tools
        data = _post(f"{self.base_url}/v1/messages", body,
                     {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        turn = LLMTurn()
        for block in data.get("content", []):
            if block.get("type") == "text":
                turn.text += block.get("text", "")
            elif block.get("type") == "tool_use":
                turn.tool_calls.append({"id": block["id"], "name": block["name"], "args": block.get("input") or {}})
        return turn


class OpenAICompatClient:
    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.name = f"openai-compatible:{model}"

    @staticmethod
    def _convert(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                                         for tc in m["tool_calls"]]
                out.append(msg)
            else:
                out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
        return out

    def complete(self, system, messages, tools, max_tokens=1200) -> LLMTurn:
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens,
                                "messages": self._convert(system, messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["input_schema"]}} for t in tools]
        data = _post(f"{self.base_url}/chat/completions", body, {"Authorization": f"Bearer {self.api_key}"})
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:200]}") from exc
        turn = LLMTurn(text=msg.get("content") or "")
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            turn.tool_calls.append({"id": tc["id"], "name": tc["function"]["name"], "args": args})
        return turn


def make_client(settings: Settings | None = None) -> LLMClient | None:
    s = settings or get_settings()
    if s.llm_provider == "anthropic":
        return AnthropicClient(s.anthropic_api_key, s.anthropic_model, s.anthropic_base_url)
    if s.llm_provider == "openai":
        return OpenAICompatClient(s.llm_api_key, s.llm_model, s.llm_base_url)
    return None
