"""FallbackProvider: try a primary provider, fall back to a second on failure.

Wraps two ordinary Providers behind the same one-method interface, so the
orchestrator never knows a fallback exists (Liskov: it's just a Provider).
The primary's own retry layer (providers/retry.py) runs first; only when a
call still fails after retries does the fallback model get the exact same
messages and tools.

Both providers are built from the same Config credentials, so the fallback
model must be reachable with them -- in practice: a smaller/cheaper model
from the same provider (e.g. fall from claude-opus to claude-haiku), or a
local key-less endpoint (e.g. ollama/...). Configured via
HARNESS_FALLBACK_MODEL; unset means no wrapper is built at all.
"""

from typing import Callable

from .base import Provider, Response

# Called when the primary fails and the fallback is about to be used.
OnFallback = Callable[[Exception], None]


class FallbackProvider(Provider):
    def __init__(
        self,
        primary: Provider,
        fallback: Provider,
        on_fallback: OnFallback | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.on_fallback = on_fallback or (lambda exc: None)

    def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        try:
            return self.primary.complete(messages, tools)
        except Exception as exc:
            # Any post-retry primary failure is grounds to fall back; if the
            # fallback also fails, *its* exception propagates -- there is
            # nothing further to fall to.
            self.on_fallback(exc)
            return self.fallback.complete(messages, tools)
