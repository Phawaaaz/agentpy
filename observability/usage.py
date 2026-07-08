"""Usage and cost tracking.

Accumulates token usage across a run and estimates spend from a small pricing
table. Prices are ESTIMATES in USD per 1,000,000 tokens and change over time —
treat the numbers as guidance, not billing. Update PRICING as needed; an unknown
model simply reports zero cost rather than guessing.
"""

from dataclasses import dataclass, field

from providers.base import Usage

# model-name substring -> (input_price_per_1M, output_price_per_1M) in USD.
# Matched by substring so "anthropic/claude-opus-4-8" hits "claude-opus".
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "o1": (15.0, 60.0),
}


def price_for(model: str) -> tuple[float, float] | None:
    for key, price in PRICING.items():
        if key in model:
            return price
    return None


def cost_for(model: str, usage: Usage) -> float:
    price = price_for(model)
    if price is None:
        return 0.0
    in_price, out_price = price
    return (
        usage.prompt_tokens / 1_000_000 * in_price
        + usage.completion_tokens / 1_000_000 * out_price
    )


@dataclass
class UsageTracker:
    """Running totals for one session/run."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    _priced: bool = field(default=True, repr=False)

    def record(self, model: str, usage: Usage | None) -> None:
        if usage is None:
            return
        self.calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        if price_for(model) is None:
            self._priced = False
        self.cost_usd += cost_for(model, usage)

    def summary(self) -> str:
        total = self.prompt_tokens + self.completion_tokens
        cost = f"${self.cost_usd:.4f}" if self._priced else f"~${self.cost_usd:.4f} (some models unpriced)"
        return (
            f"{self.calls} model calls | "
            f"{self.prompt_tokens} in + {self.completion_tokens} out = {total} tokens | "
            f"est. cost {cost}"
        )
