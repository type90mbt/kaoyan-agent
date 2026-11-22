"""模型网关：所有模型都按 OpenAI 兼容接口调用，按任务类型（chat/reason/summary/vision/embed）路由到不同模型。

DeepSeek、硅基流动、阿里云百炼、Gemini 都提供 OpenAI 兼容接口，所以这里不需要为每家写一套代码。
`mock://` 提供方用于离线体验和测试，不联网、不需要 Key。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings
from ..rag.vectors import hash_embed


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def parsed_args(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def to_message(self) -> dict[str, Any]:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class ChatResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


# 流式事件：("delta", str) | ("reasoning", str) | ("done", ChatResult)
StreamEvent = tuple[str, Any]


class Gateway:
    def __init__(
        self,
        settings_getter: Callable[[], Settings],
        key_lookup: Callable[[str], str | None],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._settings = settings_getter
        self._key = key_lookup
        self._client = httpx.Client(transport=transport, timeout=timeout)

    # ---------- 路由 ----------
    def _resolve(self, task: str) -> tuple[str, str, str | None, str]:
        settings = self._settings()
        route = settings.routes.get(task) or settings.routes.get("chat")
        if route is None:
            raise LLMError(f"没有为任务 {task} 配置模型")
        provider = settings.provider(route.provider)
        if provider is None:
            raise LLMError(f"找不到模型提供方 {route.provider}")
        return provider.base_url.rstrip("/"), route.model, self._key(provider.id), provider.id

    def available(self, task: str) -> bool:
        try:
            base, _, key, _ = self._resolve(task)
        except LLMError:
            return False
        return base.startswith("mock://") or bool(key)

    def route_info(self, task: str) -> dict[str, str]:
        base, model, _, provider_id = self._resolve(task)
        return {"provider": provider_id, "model": model, "base_url": base}

    def _headers(self, key: str | None) -> dict[str, str]:
        if not key:
            raise LLMError("没有配置这个模型提供方的 API Key，请到 设置 → 模型 中填写")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    # ---------- 对话 ----------
    def chat(
        self,
        messages: list[dict[str, Any]],
        task: str = "chat",
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        result = ChatResult()
        for kind, value in self.stream(messages, task, tools, temperature, json_mode):
            if kind == "done":
                result = value
        return result

    def stream(
        self,
        messages: list[dict[str, Any]],
        task: str = "chat",
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> Iterator[StreamEvent]:
        base, model, key, _ = self._resolve(task)
        if base.startswith("mock://"):
            yield from _mock_stream(messages, tools, model)
            return
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        body["stream_options"] = {"include_usage": True}

        result = ChatResult(model=model)
        partial: dict[int, dict[str, str]] = {}
        try:
            with self._client.stream("POST", f"{base}/chat/completions", headers=self._headers(key), json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise LLMError(f"模型接口返回 {resp.status_code}：{resp.text[:500]}")
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        result.usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            result.reasoning += delta["reasoning_content"]
                            yield ("reasoning", delta["reasoning_content"])
                        if delta.get("content"):
                            result.content += delta["content"]
                            yield ("delta", delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = partial.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise LLMError(f"连接模型接口失败：{exc}") from exc
        result.tool_calls = [
            ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=s["arguments"] or "{}")
            for i, s in sorted(partial.items())
            if s["name"]
        ]
        yield ("done", result)

    def vision(self, prompt: str, image_b64_png: str) -> str:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64_png}"}},
                ],
            }
        ]
        return self.chat(messages, task="vision").content

    # ---------- 向量化 ----------
    def embed(self, texts: list[str]) -> tuple[list[list[float]], str] | None:
        """返回 (向量列表, 模型名)。没有配置向量模型时返回 None，调用方改用本地哈希向量。"""
        if not texts or not self.available("embed"):
            return None
        base, model, key, _ = self._resolve("embed")
        if base.startswith("mock://"):
            return [hash_embed(t).tolist() for t in texts], f"mock:{model}"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 32):
            batch = texts[start : start + 32]
            try:
                resp = self._client.post(
                    f"{base}/embeddings", headers=self._headers(key), json={"model": model, "input": batch}
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"连接向量接口失败：{exc}") from exc
            if resp.status_code >= 400:
                raise LLMError(f"向量接口返回 {resp.status_code}：{resp.text[:300]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors.extend(d["embedding"] for d in data)
        return vectors, model


# ---------- 离线模拟 ----------
_TOOL_HINT = re.compile(r"/(?:tool|工具)\s+(\w+)\s*(\{.*\})?", re.S)


def _mock_stream(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, model: str) -> Iterator[StreamEvent]:
    """离线模拟：回显用户问题和检索到的资料。用户输入 `/tool 工具名 {json参数}` 可以测试工具调用。"""
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    text = last_user.get("content") if isinstance(last_user.get("content"), str) else ""
    last = messages[-1] if messages else {}
    if tools and last.get("role") == "user":
        match = _TOOL_HINT.search(text or "")
        names = {t["function"]["name"] for t in tools}
        if match and match.group(1) in names:
            call = ToolCall(id="mock_call_1", name=match.group(1), arguments=match.group(2) or "{}")
            yield ("done", ChatResult(tool_calls=[call], model=f"mock:{model}"))
            return
    if last.get("role") == "tool":
        reply = f"（离线模拟）工具已执行，结果：{str(last.get('content'))[:300]}"
    else:
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        materials = re.findall(r"<material[^>]*>(.*?)</material>", system, re.S)
        ref = f"\n\n参考资料片段：{materials[0].strip()[:200]}" if materials else ""
        reply = f"（离线模拟回答，请在 设置 → 模型 中填写 DeepSeek 等 API Key 获得真实讲解）\n你的问题是：{text[:300]}{ref}"
    for i in range(0, len(reply), 16):
        yield ("delta", reply[i : i + 16])
    yield ("done", ChatResult(content=reply, model=f"mock:{model}"))
