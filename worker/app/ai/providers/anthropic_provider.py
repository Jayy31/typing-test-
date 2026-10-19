"""Anthropic implementation of the neutral model layer (Messages API + Message Batches).

- Adaptive thinking is the model default; depth is set with `output_config.effort`.
- Client tools use `strict: true`; tool_choice stays `auto` (forced tool use is rejected by the
  current models), so the prompt tells the model which tool to call.
- Web search / fetch are server tools restricted to `allowed_domains`; the tool version per model
  comes from config/research_agent.yaml `server_tools`.
- `pause_turn` (server-side loop limit) is resumed by re-sending the history unchanged.
- The whole conversation is cached with top-level automatic prompt caching.
"""

import json
import time
from typing import Any, cast

import anthropic
from pydantic import BaseModel

from app.ai.providers.base import (
    StructuredRequest,
    StructuredResult,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
    WebSource,
    WebTools,
)
from app.yaml_config import load_yaml

MAX_PAUSE_RESUMES = 3
STOP = {
    "end_turn": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "pause_turn": "paused",
    "stop_sequence": "end",
}


def _schema(x: "dict[str, Any] | type[BaseModel]") -> dict[str, Any]:
    if isinstance(x, dict):
        return x
    return anthropic.transform_schema(x)


def server_tool_types(model: str) -> dict[str, str]:
    st = load_yaml("research_agent.yaml")["server_tools"]
    return dict(st.get(model) or st["default"])


def web_tool_params(model: str, web: WebTools) -> list[dict[str, Any]]:
    if not web.enabled:
        return []
    types = server_tool_types(model)
    domains = sorted(set(web.allowed_domains))
    out: list[dict[str, Any]] = []
    if web.max_searches > 0:
        out.append(
            {
                "type": types["web_search"],
                "name": "web_search",
                "max_uses": web.max_searches,
                "allowed_domains": domains,
            }
        )
    if web.max_fetches > 0:
        out.append(
            {
                "type": types["web_fetch"],
                "name": "web_fetch",
                "max_uses": web.max_fetches,
                "allowed_domains": domains,
                "max_content_tokens": web.fetch_max_content_tokens,
                "citations": {"enabled": True},
            }
        )
    return out


def usage_of(u: Any) -> Usage:
    stu = getattr(u, "server_tool_use", None)
    return Usage(
        input_tokens=int(getattr(u, "input_tokens", 0) or 0),
        output_tokens=int(getattr(u, "output_tokens", 0) or 0),
        cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0),
        cache_write=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        web_searches=int(getattr(stu, "web_search_requests", 0) or 0) if stu else 0,
        web_fetches=int(getattr(stu, "web_fetch_requests", 0) or 0) if stu else 0,
    )


def _dump(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    return cast(dict[str, Any], block.to_dict())


def read_content(content: list[Any]) -> tuple[str, list[ToolCall], list[WebSource], list[str]]:
    """Text, client tool calls, web sources and search queries from one response."""
    texts: list[str] = []
    calls: list[ToolCall] = []
    sources: list[WebSource] = []
    queries: list[str] = []
    for b in content:
        t = getattr(b, "type", None)
        if t == "text":
            texts.append(b.text)
        elif t == "tool_use":
            calls.append(ToolCall(b.id, b.name, dict(b.input or {})))
        elif t == "server_tool_use" and b.name == "web_search":
            q = (b.input or {}).get("query")
            if q:
                queries.append(str(q))
        elif t == "web_search_tool_result":
            items = b.content if isinstance(b.content, list) else []
            for r in items:
                url = getattr(r, "url", None)
                if url:
                    sources.append(WebSource(url, getattr(r, "title", "") or "", "search"))
        elif t == "web_fetch_tool_result":
            url = getattr(b.content, "url", None)
            if url:
                doc = getattr(b.content, "content", None)
                sources.append(WebSource(url, getattr(doc, "title", "") or "", "fetch"))
    return "\n".join(texts).strip(), calls, sources, queries


class AnthropicConversation:
    def __init__(
        self,
        messages_api: Any,
        model: str,
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
        effort: str | None,
        timeout_s: float,
    ) -> None:
        self.api, self.model, self.system, self.tools = messages_api, model, system, tools
        self.max_tokens, self.effort, self.timeout_s = max_tokens, effort, timeout_s
        self.history: list[dict[str, Any]] = []

    def _request(self) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": self.system}],
            "messages": list(self.history),
            "cache_control": {"type": "ephemeral"},
            "timeout": self.timeout_s,
        }
        if self.tools:
            kwargs["tools"] = self.tools
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        return self.api.create(**kwargs)

    def send(self, text: str | None = None, tool_results: list[ToolResult] | None = None) -> Turn:
        content: list[dict[str, Any]] = [
            {
                "type": "tool_result",
                "tool_use_id": r.call_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in (tool_results or [])
        ]
        if text:
            content.append({"type": "text", "text": text})
        if not content:
            raise ValueError("send() needs text or tool results")
        self.history.append({"role": "user", "content": content})
        t0 = time.monotonic()
        usage = Usage()
        texts: list[str] = []
        calls: list[ToolCall] = []
        sources: list[WebSource] = []
        queries: list[str] = []
        stop = "end"
        for _ in range(MAX_PAUSE_RESUMES + 1):
            resp = self._request()
            usage.accumulate(usage_of(resp.usage))
            self.history.append({"role": "assistant", "content": [_dump(b) for b in resp.content]})
            tx, cl, so, qu = read_content(resp.content)
            if tx:
                texts.append(tx)
            calls += cl
            sources += so
            queries += qu
            stop = STOP.get(str(resp.stop_reason), "end")
            if stop != "paused":
                break
        return Turn(
            "\n".join(texts), calls, stop, usage, sources, queries, round(time.monotonic() - t0, 2)
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, client: Any = None) -> None:
        self._client = client
        self._api_key = api_key

    def client(self) -> Any:
        if self._client is None:
            from app.config import get_settings

            self._client = anthropic.Anthropic(
                api_key=self._api_key or get_settings().anthropic_api_key or None, timeout=120.0
            )
        return self._client

    def start(
        self,
        *,
        model: str,
        system: str,
        tools: list[ToolSpec],
        web: WebTools,
        max_tokens: int,
        effort: str | None,
        timeout_s: float,
    ) -> AnthropicConversation:
        params = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": _schema(t.input_schema),
                "strict": t.strict,
            }
            for t in tools
        ] + web_tool_params(model, web)
        return AnthropicConversation(
            self.client().messages, model, system, params, max_tokens, effort, timeout_s
        )

    def _params(self, req: StructuredRequest) -> dict[str, Any]:
        oc: dict[str, Any] = {"format": {"type": "json_schema", "schema": _schema(req.schema)}}
        if req.effort:
            oc["effort"] = req.effort
        p: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": req.user}],
            "output_config": oc,
        }
        tools = web_tool_params(req.model, req.web)
        if tools:
            p["tools"] = tools
        return p

    @staticmethod
    def _result(req_schema: type[BaseModel], msg: Any) -> StructuredResult:
        usage = usage_of(msg.usage)
        text, _, sources, _ = read_content(msg.content)
        if msg.stop_reason == "refusal":
            return StructuredResult(None, usage, sources, "model refused the request")
        last = next((b.text for b in reversed(msg.content) if getattr(b, "type", "") == "text"), "")
        try:
            data = req_schema.model_validate(json.loads(last)).model_dump(mode="json")
        except (ValueError, TypeError) as exc:
            return StructuredResult(None, usage, sources, f"invalid output: {exc}"[:300])
        return StructuredResult(data, usage, sources)

    def structured(self, req: StructuredRequest, timeout_s: float = 180.0) -> StructuredResult:
        params = self._params(req)
        usage = Usage()
        msg: Any = None
        for _ in range(MAX_PAUSE_RESUMES + 1):
            msg = self.client().messages.create(**params, timeout=timeout_s)
            usage.accumulate(usage_of(msg.usage))
            if msg.stop_reason != "pause_turn":
                break
            params["messages"] = params["messages"] + [
                {"role": "assistant", "content": [_dump(b) for b in msg.content]}
            ]
        res = self._result(req.schema, msg)
        res.usage = usage
        return res

    def submit_batch(self, reqs: list[StructuredRequest]) -> str:
        self._schemas = {r.custom_id: r.schema for r in reqs}
        batch = self.client().messages.batches.create(
            requests=[{"custom_id": r.custom_id, "params": self._params(r)} for r in reqs]
        )
        return str(batch.id)

    def batch_done(self, batch_id: str) -> bool:
        return bool(self.client().messages.batches.retrieve(batch_id).processing_status == "ended")

    def batch_results(
        self, batch_id: str, schemas: dict[str, type[BaseModel]] | None = None
    ) -> dict[str, StructuredResult]:
        schemas = schemas or getattr(self, "_schemas", {})
        out: dict[str, StructuredResult] = {}
        for r in self.client().messages.batches.results(batch_id):
            if r.result.type != "succeeded":
                out[r.custom_id] = StructuredResult(None, Usage(), [], f"batch {r.result.type}")
                continue
            schema = schemas.get(r.custom_id)
            if schema is None:
                out[r.custom_id] = StructuredResult(
                    None, usage_of(r.result.message.usage), [], "unknown custom_id"
                )
                continue
            out[r.custom_id] = self._result(schema, r.result.message)
        return out
