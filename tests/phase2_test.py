"""Phase 2 tests: context compaction, session persistence, usage tracking.

All use fakes — no API key, no network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from context_engine.compaction import Conversation
from context_engine.session_store import SessionStore
from engine.orchestrator import Orchestrator
from engine.registry import registry
from observability.usage import UsageTracker, cost_for
from providers.base import Provider, Response, Usage

import engine.builtin.filesystem  # noqa: F401


class FakeProvider(Provider):
    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def complete(self, messages, tools):
        turn = self._script[self._i]
        self._i += 1
        return turn


def _final(text, usage=None):
    return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text}, usage=usage)


def test_usage_tracking():
    provider = FakeProvider([_final("done", Usage(prompt_tokens=1000, completion_tokens=500))])
    tracker = UsageTracker()
    agent = Orchestrator(
        provider, registry, Config(model="anthropic/claude-opus-4-8"),
        usage_tracker=tracker,
    )
    agent.run("hi")
    assert tracker.calls == 1
    assert tracker.prompt_tokens == 1000 and tracker.completion_tokens == 500
    # opus pricing: 1000/1e6*15 + 500/1e6*75 = 0.015 + 0.0375
    assert abs(tracker.cost_usd - 0.0525) < 1e-9, tracker.cost_usd
    print("  usage tracking OK:", tracker.summary())


def test_context_compaction():
    calls = {"n": 0}

    def fake_summarizer(previous, messages):
        calls["n"] += 1
        return f"SUMMARY(prev={bool(previous)}, folded={len(messages)})"

    convo = Conversation(
        "SYS", max_context_tokens=50, keep_recent_messages=2, summarizer=fake_summarizer
    )
    # An assistant tool-call followed by its tool result, then filler.
    convo.add({"role": "user", "content": "x" * 400})
    convo.add({"role": "assistant", "content": "", "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
    convo.add({"role": "tool", "tool_call_id": "t1", "content": "y" * 400})
    convo.add({"role": "user", "content": "recent-1"})
    convo.add({"role": "assistant", "content": "recent-2"})

    assert convo.estimated_tokens() > 50
    did = convo.maybe_compact()
    assert did and calls["n"] == 1
    assert convo.summary.startswith("SUMMARY")
    # The kept slice must not start with an orphaned tool result.
    assert convo.messages[0]["role"] != "tool"
    # Recent messages preserved.
    assert convo.messages[-1]["content"] == "recent-2"
    print("  context compaction OK: kept", len(convo.messages), "recent messages")


def test_session_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        store = SessionStore(d)
        a = Conversation("SYS")
        a.add({"role": "user", "content": "remember this"})
        a.summary = "earlier stuff"
        store.save("sess1", a)

        b = Conversation("OTHER")
        assert store.load("sess1", b)
        assert b.messages == [{"role": "user", "content": "remember this"}]
        assert b.summary == "earlier stuff"
        assert b.system_prompt == "SYS"
        assert store.list_ids() == ["sess1"]
        assert store.load("missing", b) is False
    print("  session round-trip OK")


def main():
    test_usage_tracking()
    test_context_compaction()
    test_session_roundtrip()
    print("PHASE 2 TESTS PASSED")


if __name__ == "__main__":
    main()
