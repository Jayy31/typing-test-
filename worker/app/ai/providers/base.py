"""Neutral types for messages, tool calls, structured output and usage.

A provider turns these into its own API calls. The agent loop never sees provider objects:
a `Conversation` keeps the provider-native history (thinking blocks, server-tool results) and
returns a neutral `Turn` for each request.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    """A client tool the model may call (our read-only data tools, the submit tool)."""

    name: str
    description: str
    input_schema: "dict[str, Any] | type[BaseModel]"  # JSON schema or a Pydantic model
    strict: bool = True


@dataclass(frozen=True)
class WebTools:
    """Provider-hosted web tools. Off unless `enabled`; domains always restricted."""

    enabled: bool = False
    max_searches: int = 0
    max_fetches: int = 0
    allowed_domains: tuple[str, ...] = ()
    fetch_max_content_tokens: int = 12000


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0  # includes thinking
    cache_read: int = 0
    cache_write: int = 0
    web_searches: int = 0
    web_fetches: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_write

    def accumulate(self, other: "Usage") -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))


@dataclass(frozen=True)
class WebSource:
    url: str
    title: str = ""
    kind: str = "search"  # search | fetch


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    stop: str  # end | tool_use | max_tokens | refusal | paused
    usage: Usage
    sources: list[WebSource] = field(default_factory=list)
    web_queries: list[str] = field(default_factory=list)
    duration_s: float = 0.0


@dataclass(frozen=True)
class StructuredRequest:
    """One structured call (also one Message Batch entry)."""

    custom_id: str
    model: str
    system: str
    user: str
    schema: type[BaseModel]
    max_tokens: int
    effort: str | None = None
    web: WebTools = WebTools()


@dataclass
class StructuredResult:
    data: dict[str, Any] | None
    usage: Usage
    sources: list[WebSource] = field(default_factory=list)
    error: str | None = None


class Conversation(Protocol):
    def send(
        self, text: str | None = None, tool_results: list[ToolResult] | None = None
    ) -> Turn: ...


class ModelProvider(Protocol):
    name: str

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
    ) -> Conversation: ...

    def structured(self, req: StructuredRequest, timeout_s: float = 180.0) -> StructuredResult: ...

    def submit_batch(self, reqs: list[StructuredRequest]) -> str: ...

    def batch_done(self, batch_id: str) -> bool: ...

    def batch_results(
        self, batch_id: str, schemas: dict[str, type[BaseModel]] | None = None
    ) -> dict[str, StructuredResult]: ...
