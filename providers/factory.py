"""Provider factory — turns a model string into the right Provider.

This is what keeps the harness model-independent. The model string is
"<provider>/<model>"; the prefix selects the implementation. OpenAI-compatible
providers (Ollama, OpenRouter, Groq, Together, ...) reuse the OpenAI client with
a known base_url, so "any model, just plug in a key" holds.
"""

from config import Config
from providers.anthropic_provider import AnthropicProvider
from providers.base import Provider
from providers.openai_provider import OpenAIProvider

# Prefixes that are really OpenAI-compatible endpoints, with their base URLs.
OPENAI_COMPATIBLE = {
    "openai": None,  # the real OpenAI API
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
}


def build_provider(config: Config) -> Provider:
    if "/" in config.model:
        prefix, model_name = config.model.split("/", 1)
    else:
        prefix, model_name = "", config.model

    if prefix == "anthropic":
        return AnthropicProvider(
            model=model_name,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

    if prefix in OPENAI_COMPATIBLE:
        # A base_url from config wins; otherwise use the prefix's known URL.
        base_url = config.base_url or OPENAI_COMPATIBLE[prefix]
        # Ollama needs a non-empty key string but ignores its value.
        api_key = config.api_key or ("ollama" if prefix == "ollama" else None)
        return OpenAIProvider(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=config.temperature,
        )

    # Unknown prefix but a base_url is set => treat it as OpenAI-compatible.
    if config.base_url:
        return OpenAIProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=config.temperature,
        )

    raise ValueError(
        f"Don't know how to build a provider for model '{config.model}'. "
        f"Use a known prefix ({', '.join(['anthropic', *OPENAI_COMPATIBLE])}) "
        f"or set HARNESS_BASE_URL for a custom OpenAI-compatible endpoint."
    )
