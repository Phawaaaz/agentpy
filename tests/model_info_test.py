"""Tests for providers/model_info.py and its wiring through the factory --
per-model context windows and output limits, with explicit config always
winning and unknown models falling back to the historical defaults.
No key, no network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from providers.factory import build_provider
from providers.fallback import FallbackProvider
from providers.model_info import (
    FALLBACK_CONTEXT_BUDGET,
    FALLBACK_MAX_OUTPUT_TOKENS,
    effective_context_budget,
    effective_max_tokens,
    info_for,
)


def test_substring_lookup():
    assert info_for("anthropic/claude-opus-4-8").context_window == 200_000
    assert info_for("openai/gpt-4o-mini").max_output_tokens == 16_384
    assert info_for("ollama/some-unknown-model") is None
    print("  substring lookup (known hit, unknown miss) OK")


def test_explicit_override_always_wins():
    assert effective_max_tokens("anthropic/claude-opus-4-8", 1234) == 1234
    assert effective_context_budget("anthropic/claude-opus-4-8", 5678) == 5678
    print("  explicit config values override the table OK")


def test_known_model_uses_table():
    assert effective_max_tokens("anthropic/claude-haiku-x", None) == 8_192
    # Budget is a safety fraction of the window, never the full window.
    budget = effective_context_budget("anthropic/claude-sonnet-x", None)
    assert 0 < budget < 200_000
    print("  known model derives limits from the table OK")


def test_unknown_model_uses_historical_defaults():
    assert effective_max_tokens("ollama/mystery", None) == FALLBACK_MAX_OUTPUT_TOKENS == 4_096
    assert effective_context_budget("ollama/mystery", None) == FALLBACK_CONTEXT_BUDGET == 100_000
    print("  unknown model falls back to the historical 4096/100k defaults OK")


def test_factory_resolves_anthropic_max_tokens():
    provider = build_provider(Config(model="anthropic/claude-haiku-x", api_key="test"))
    assert provider.max_tokens == 8_192, provider.max_tokens
    provider = build_provider(
        Config(model="anthropic/claude-haiku-x", api_key="test", max_tokens=999)
    )
    assert provider.max_tokens == 999
    print("  factory passes model-resolved max_tokens to the Anthropic adapter OK")


def test_factory_wraps_fallback_model():
    config = Config(model="anthropic/claude-haiku-x", api_key="test", fallback_model="ollama/llama3")
    provider = build_provider(config)
    assert isinstance(provider, FallbackProvider)
    # No fallback configured -> no wrapper.
    plain = build_provider(Config(model="anthropic/claude-haiku-x", api_key="test"))
    assert not isinstance(plain, FallbackProvider)
    print("  factory wraps a FallbackProvider only when fallback_model is set OK")


def main():
    test_substring_lookup()
    test_explicit_override_always_wins()
    test_known_model_uses_table()
    test_unknown_model_uses_historical_defaults()
    test_factory_resolves_anthropic_max_tokens()
    test_factory_wraps_fallback_model()
    print("MODEL INFO TESTS PASSED")


if __name__ == "__main__":
    main()
