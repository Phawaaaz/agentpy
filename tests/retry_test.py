"""Tests for providers/retry.py and providers/fallback.py -- transient-error
retry with backoff, and the optional fallback model wrapper. No key, no
network, no real sleeping (the sleep function is injected).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.base import Provider, Response
from providers.fallback import FallbackProvider
from providers.retry import call_with_retries


class _Flaky:
    """Fails `failures` times with `exc_type`, then returns `value`."""

    def __init__(self, failures: int, exc_type: type[Exception], value: str = "ok"):
        self.failures = failures
        self.exc_type = exc_type
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc_type(f"transient failure #{self.calls}")
        return self.value


def test_succeeds_after_transient_failures():
    slept = []
    flaky = _Flaky(failures=2, exc_type=ConnectionError)
    result = call_with_retries(
        flaky, (ConnectionError,), attempts=3, base_delay_s=1.0, sleep=slept.append
    )
    assert result == "ok"
    assert flaky.calls == 3
    assert slept == [1.0, 2.0], f"expected exponential backoff, got {slept}"
    print("  retries transient failures with exponential backoff OK")


def test_raises_after_exhausting_attempts():
    slept = []
    flaky = _Flaky(failures=5, exc_type=ConnectionError)
    try:
        call_with_retries(
            flaky, (ConnectionError,), attempts=3, base_delay_s=1.0, sleep=slept.append
        )
    except ConnectionError as exc:
        assert "failure #3" in str(exc), "should re-raise the final attempt's error"
    else:
        raise AssertionError("expected the final ConnectionError to propagate")
    assert flaky.calls == 3
    assert slept == [1.0, 2.0], "no sleep after the final attempt"
    print("  re-raises the final error after exhausting attempts OK")


def test_non_retryable_errors_fail_immediately():
    flaky = _Flaky(failures=1, exc_type=ValueError)
    try:
        call_with_retries(flaky, (ConnectionError,), attempts=3, sleep=lambda s: None)
    except ValueError:
        pass
    else:
        raise AssertionError("expected the ValueError to propagate immediately")
    assert flaky.calls == 1, "a non-retryable error must not be retried"
    print("  non-retryable errors fail immediately (no retry) OK")


class _FakeProvider(Provider):
    def __init__(self, fail_with: Exception | None = None, reply: str = "hi"):
        self.fail_with = fail_with
        self.reply = reply
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return Response(
            text=self.reply,
            tool_calls=[],
            assistant_message={"role": "assistant", "content": self.reply},
        )


def test_fallback_unused_when_primary_succeeds():
    primary = _FakeProvider(reply="primary")
    fallback = _FakeProvider(reply="fallback")
    provider = FallbackProvider(primary, fallback)
    response = provider.complete([], [])
    assert response.text == "primary"
    assert fallback.calls == 0, "fallback must not be called when the primary works"
    print("  fallback untouched while the primary succeeds OK")


def test_fallback_used_when_primary_fails():
    primary = _FakeProvider(fail_with=ConnectionError("rate limited"))
    fallback = _FakeProvider(reply="fallback")
    observed = []
    provider = FallbackProvider(primary, fallback, on_fallback=observed.append)
    response = provider.complete([], [])
    assert response.text == "fallback"
    assert primary.calls == 1 and fallback.calls == 1
    assert len(observed) == 1 and isinstance(observed[0], ConnectionError)
    print("  primary failure falls through to the fallback (and reports it) OK")


def test_fallback_failure_propagates():
    primary = _FakeProvider(fail_with=ConnectionError("down"))
    fallback = _FakeProvider(fail_with=RuntimeError("also down"))
    provider = FallbackProvider(primary, fallback)
    try:
        provider.complete([], [])
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the fallback's own error to propagate")
    print("  when both fail, the fallback's error propagates OK")


def test_fallback_chain_falls_through_multiple_levels():
    """A comma-separated HARNESS_FALLBACK_MODEL builds a nested chain: the
    factory folds one FallbackProvider per level, so a dead primary + dead
    first fallback still lands on the last one."""
    from config import Config
    from providers.factory import _fallback_chain

    assert _fallback_chain("a/b, c/d") == ["a/b", "c/d"]
    assert _fallback_chain(None) == [] and _fallback_chain("") == []

    a = _FakeProvider(fail_with=ConnectionError("gemma down"))
    b = _FakeProvider(fail_with=ConnectionError("gemini down"))
    c = _FakeProvider(reply="groq")
    # mimic build_provider's left fold: primary -> b -> c
    chain = FallbackProvider(FallbackProvider(a, b), c)
    assert chain.complete([], []).text == "groq"
    assert a.calls == 1 and b.calls == 1 and c.calls == 1
    print("  3-model fallback chain falls through to the last model OK")


def test_fallback_stream_switches_before_first_token():
    """Streaming falls back when the primary dies on its first event, and
    preserves the fallback's deltas (keeps the live-typing effect)."""
    class _StreamProvider(Provider):
        def __init__(self, ok, reply="x"):
            self.ok, self.reply = ok, reply
        def complete(self, m, t):
            return Response(text=self.reply, tool_calls=[],
                            assistant_message={"role": "assistant", "content": self.reply})
        def stream(self, m, t):
            if not self.ok:
                raise ConnectionError("primary down")
                yield  # unreachable; makes this a generator
            for w in ["he", "llo"]:
                yield ("delta", w)
            yield ("response", self.complete(m, t))

    chain = FallbackProvider(_StreamProvider(ok=False), _StreamProvider(ok=True, reply="hello"))
    events = list(chain.stream([], []))
    assert [d for k, d in events if k == "delta"] == ["he", "llo"]
    assert [d.text for k, d in events if k == "response"] == ["hello"]
    print("  streaming falls back before first token and keeps deltas OK")


def main():
    test_succeeds_after_transient_failures()
    test_raises_after_exhausting_attempts()
    test_non_retryable_errors_fail_immediately()
    test_fallback_unused_when_primary_succeeds()
    test_fallback_used_when_primary_fails()
    test_fallback_failure_propagates()
    test_fallback_chain_falls_through_multiple_levels()
    test_fallback_stream_switches_before_first_token()
    print("RETRY/FALLBACK TESTS PASSED")


if __name__ == "__main__":
    main()
