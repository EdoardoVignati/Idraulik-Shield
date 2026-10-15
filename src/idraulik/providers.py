"""LLM providers. Internal message format is OpenAI-like:
 {"role", "content", "tool_calls": [{"id","name","arguments"(dict)}], "tool_call_id", "name"}
Each provider returns {"content": str, "tool_calls": [...]}."""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

from .config import ProviderConfig


class ProviderError(RuntimeError):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:  # network, HTTP, JSON
        raise ProviderError(f"{url}: {e}") from e


class Provider:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        raise NotImplementedError


class OllamaProvider(Provider):
    def chat(self, messages, tools):
        msgs = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m.get("content") or "",
                             "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                            for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "content": m["content"], "tool_name": m.get("name", "")})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        headers = {}
        if self.cfg.api_key_env and os.environ.get(self.cfg.api_key_env):
            headers["Authorization"] = f"Bearer {os.environ[self.cfg.api_key_env]}"
        out = _post(self.cfg.base_url.rstrip("/") + "/api/chat",
                    {"model": self.cfg.model, "messages": msgs, "tools": tools, "stream": False},
                    headers, self.cfg.timeout)
        msg = out.get("message", {})
        calls = [{"id": uuid.uuid4().hex[:8], "name": c["function"]["name"],
                  "arguments": c["function"].get("arguments") or {}}
                 for c in msg.get("tool_calls") or []]
        return {"content": msg.get("content", ""), "tool_calls": calls}


class OpenAIProvider(Provider):
    """Any OpenAI-compatible API: OpenAI, OpenRouter, Groq, vLLM, LM Studio, Anthropic compat endpoint..."""

    def chat(self, messages, tools):
        msgs = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m.get("content") or None,
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"],
                                                          "arguments": json.dumps(c["arguments"])}}
                                            for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        key = os.environ.get(self.cfg.api_key_env, "") if self.cfg.api_key_env else ""
        if self.cfg.api_key_env and not key:
            raise ProviderError(f"env var {self.cfg.api_key_env} is empty (see secrets.env)")
        out = _post(self.cfg.base_url.rstrip("/") + "/chat/completions",
                    {"model": self.cfg.model, "messages": msgs, "tools": tools},
                    {"Authorization": f"Bearer {key}"} if key else {}, self.cfg.timeout)
        try:
            msg = out["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"bad response: {out}") from e
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": c["id"], "name": c["function"]["name"], "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}


def _require_key(cfg: ProviderConfig) -> str:
    key = os.environ.get(cfg.api_key_env, "")
    if not key:
        raise ProviderError(f"env var {cfg.api_key_env} is empty (see secrets.env)")
    return key


class AnthropicProvider(Provider):
    """Claude via the native Messages API."""

    def chat(self, messages, tools):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append(block)
                else:
                    msgs.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                    for c in m.get("tool_calls") or []]
                msgs.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "..."}]})
            else:
                msgs.append({"role": "user", "content": m["content"]})
        payload = {"model": self.cfg.model, "max_tokens": 2048, "system": system, "messages": msgs}
        if tools:
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                                 "input_schema": t["function"]["parameters"]} for t in tools]
        out = _post(self.cfg.base_url.rstrip("/") + "/v1/messages", payload,
                    {"x-api-key": _require_key(self.cfg), "anthropic-version": "2023-06-01"}, self.cfg.timeout)
        blocks = out.get("content")
        if blocks is None:
            raise ProviderError(f"bad response: {out}")
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input") or {}}
                 for b in blocks if b.get("type") == "tool_use"]
        return {"content": text, "tool_calls": calls}


class GeminiProvider(Provider):
    """Google Gemini via generateContent."""

    def chat(self, messages, tools):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                part = {"functionResponse": {"name": m.get("name", ""), "response": {"result": m["content"]}}}
                if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif m["role"] == "assistant":
                parts = ([{"text": m["content"]}] if m.get("content") else []) + [
                    {"functionCall": {"name": c["name"], "args": c["arguments"]}} for c in m.get("tool_calls") or []]
                contents.append({"role": "model", "parts": parts or [{"text": "..."}]})
            else:
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        payload: dict = {"contents": contents, "systemInstruction": {"parts": [{"text": system}]}}
        if tools:
            decls = []
            for t in tools:
                f = dict(t["function"])
                if not f["parameters"]["properties"]:
                    f.pop("parameters")  # Gemini rejects empty object schemas
                decls.append(f)
            payload["tools"] = [{"functionDeclarations": decls}]
        url = f"{self.cfg.base_url.rstrip('/')}/v1beta/models/{self.cfg.model}:generateContent"
        out = _post(url, payload, {"x-goog-api-key": _require_key(self.cfg)}, self.cfg.timeout)
        try:
            parts = out["candidates"][0]["content"].get("parts", [])
        except (KeyError, IndexError) as e:
            raise ProviderError(f"bad response: {out}") from e
        text = "".join(p.get("text", "") for p in parts)
        calls = [{"id": uuid.uuid4().hex[:8], "name": p["functionCall"]["name"],
                  "arguments": p["functionCall"].get("args") or {}} for p in parts if "functionCall" in p]
        return {"content": text, "tool_calls": calls}


PROVIDER_TYPES = {"ollama": OllamaProvider, "openai": OpenAIProvider,
                  "anthropic": AnthropicProvider, "gemini": GeminiProvider}


def build_provider(cfg: ProviderConfig) -> Provider:
    if cfg.type not in PROVIDER_TYPES:
        raise ValueError(f"unknown provider type '{cfg.type}' (use: {', '.join(PROVIDER_TYPES)})")
    return PROVIDER_TYPES[cfg.type](cfg)
