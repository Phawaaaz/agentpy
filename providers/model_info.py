"""Per-model metadata: context window and max output tokens.

Same shape and matching convention as observability/usage.py's PRICING table
(substring match, so "anthropic/claude-opus-4-8" hits "claude-opus"). An
unknown model falls back to conservative defaults rather than guessing high
-- exactly like an unpriced model reports zero cost rather than a made-up
number. Values drift as vendors update limits; treat them as good defaults,
not hard API truths, and update the table as needed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    context_window: int  # total input context the model accepts, in tokens
    max_output_tokens: int  # the most it can generate in one turn


# model-name substring -> ModelInfo. First match wins; keep more-specific
# keys (e.g. "gpt-4o-mini") above their prefixes ("gpt-4o").
MODEL_INFO: dict[str, ModelInfo] = {
    "claude-opus": ModelInfo(context_window=200_000, max_output_tokens=32_000),
    "claude-sonnet": ModelInfo(context_window=200_000, max_output_tokens=64_000),
    "claude-haiku": ModelInfo(context_window=200_000, max_output_tokens=8_192),
    "gpt-4o-mini": ModelInfo(context_window=128_000, max_output_tokens=16_384),
    "gpt-4o": ModelInfo(context_window=128_000, max_output_tokens=16_384),
    "o1": ModelInfo(context_window=200_000, max_output_tokens=100_000),
}

# Fallbacks for unknown models -- match the harness's historical hardcoded
# defaults exactly, so an unrecognized model behaves the same as before this
# table existed.
FALLBACK_CONTEXT_BUDGET = 100_000
FALLBACK_MAX_OUTPUT_TOKENS = 4_096

# A known window is not all usable for input: the model's output shares it,
# and our token estimate (context_engine/compaction.py) is approximate. Only
# budget this fraction of a known window for history before compacting.
CONTEXT_BUDGET_FRACTION = 0.8


def info_for(model: str) -> ModelInfo | None:
    """The ModelInfo whose key appears in `model`, or None if unknown."""
    for key, info in MODEL_INFO.items():
        if key in model:
            return info
    return None


def effective_max_tokens(model: str, override: int | None) -> int:
    """Max output tokens for one turn: the user's explicit setting if given,
    else the model's known limit, else the historical 4096 default."""
    if override is not None:
        return override
    info = info_for(model)
    return info.max_output_tokens if info else FALLBACK_MAX_OUTPUT_TOKENS


def effective_context_budget(model: str, override: int | None) -> int:
    """Token budget that triggers history compaction: the user's explicit
    setting if given, else a safe fraction of the model's known window,
    else the historical 100k default."""
    if override is not None:
        return override
    info = info_for(model)
    if info is None:
        return FALLBACK_CONTEXT_BUDGET
    return int(info.context_window * CONTEXT_BUDGET_FRACTION)
