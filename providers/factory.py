"""Provider factory — turns a model string into the right Provider.

This is what keeps the harness model-independent. The model string is
"<provider>/<model>"; the prefix selects the implementation. OpenAI-compatible
providers (Ollama, OpenRouter, Groq, Together, ...) reuse the OpenAI client with
a known base_url, so "any model, just plug in a key" holds.

If config.fallback_model is set, the returned Provider is a FallbackProvider
wrapping two ordinary providers — the orchestrator can't tell the difference
(it's still just a Provider with one `complete` method).
"""

from config import Config
from providers.anthropic_provider import AnthropicProvider
from providers.base import Provider
from providers.fallback import FallbackProvider
from providers.model_info import effective_max_tokens
from providers.openai_provider import OpenAIProvider

# Prefixes that are really OpenAI-compatible endpoints, with their base URLs.
OPENAI_COMPATIBLE = {
    "openai": None,  # the real OpenAI API
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
}


def build_provider(config: Config) -> Provider:
    provider = _build_for_model(config, config.model)
    if config.fallback_model:
        fallback = _build_for_model(config, config.fallback_model)
        return FallbackProvider(provider, fallback)
    return provider


def _build_for_model(config: Config, model: str) -> Provider:
    """Build one concrete provider for `model`, using `config` for
    credentials and tuning. Split out of build_provider so the primary and
    fallback models go through identical resolution."""
    import os
    if "/" in model:
        prefix, model_name = model.split("/", 1)
    else:
        prefix, model_name = "", model

    # Dynamically select key if prefix-specific environment variable is set
    prefix_key = None
    if prefix == "gemini":
        prefix_key = os.getenv("GEMINI_API_KEY")
    elif prefix == "openai":
        prefix_key = os.getenv("OPENAI_API_KEY")
    elif prefix == "anthropic":
        prefix_key = os.getenv("ANTHROPIC_API_KEY")

    resolved_api_key = prefix_key or config.api_key

    if prefix == "anthropic":
        return AnthropicProvider(
            model=model_name,
            api_key=resolved_api_key,
            # None means "the model's known output limit" (model_info.py).
            max_tokens=effective_max_tokens(model, config.max_tokens),
            temperature=config.temperature,
        )

    if prefix in OPENAI_COMPATIBLE:
        # A base_url from config wins; otherwise use the prefix's known URL.
        base_url = config.base_url or OPENAI_COMPATIBLE[prefix]
        # Ollama needs a non-empty key string but ignores its value.
        api_key = resolved_api_key or ("ollama" if prefix == "ollama" else None)
        return OpenAIProvider(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=config.temperature,
        )

    # Unknown prefix but a base_url is set => treat it as OpenAI-compatible.
    if config.base_url:
        return OpenAIProvider(
            model=model,
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=config.temperature,
        )

    raise ValueError(
        f"Don't know how to build a provider for model '{model}'. "
        f"Use a known prefix ({', '.join(['anthropic', *OPENAI_COMPATIBLE])}) "
        f"or set HARNESS_BASE_URL for a custom OpenAI-compatible endpoint."
    )
