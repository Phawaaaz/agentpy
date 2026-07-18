"""FallbackProvider: try a primary provider, fall back to a second on failure.

Wraps two ordinary Providers behind the same one-method interface, so the
orchestrator never knows a fallback exists (Liskov: it's just a Provider).
The primary's own retry layer (providers/retry.py) runs first; only when a
call still fails after retries does the fallback model get the exact same
messages and tools.

Each provider is built with the credentials it needs (the factory resolves a
per-prefix key + base_url), so a fallback can be a different vendor entirely
-- e.g. a private model as primary, a hosted model as backup. Configured via
HARNESS_FALLBACK_MODEL. That value may name several models separated by
commas ("gemini/...,groq/..."): the factory nests one FallbackProvider per
level, so the chain is tried left to right until one succeeds. Unset means no
wrapper is built at all.
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

    def stream(self, messages: list[dict], tools: list[dict]):
        """Stream from the primary; fall back on a failure that happens
        before any output is produced. Most real failures (bad model, auth,
        connection) surface on the first call, so we pull the first event
        eagerly: if that raises, we switch to the fallback and stream it
        instead. Once the primary has emitted anything we're committed to it
        -- a later error can't be recovered without double-printing, so it
        propagates. `self.fallback` may itself be a FallbackProvider, so the
        recursion walks a whole chain."""
        try:
            gen = self.primary.stream(messages, tools)
            first = next(gen)
        except StopIteration:
            return  # primary produced nothing at all; treat as done
        except Exception as exc:
            self.on_fallback(exc)
            yield from self.fallback.stream(messages, tools)
            return
        yield first
        yield from gen
