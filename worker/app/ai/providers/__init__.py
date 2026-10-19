"""Provider-neutral model layer (Phase 13). Agent code talks only to `base`; each provider is one
module plus config. Only Anthropic is implemented."""

from app.ai.providers.base import ModelProvider


def get_provider(name: str, **kwargs: object) -> ModelProvider:
    if name == "anthropic":
        from app.ai.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(**kwargs)  # type: ignore[arg-type]
    raise ValueError(f"unknown AI provider {name!r} (implemented: anthropic)")
