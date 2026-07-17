"""Token-streaming tests (D35): the Provider.stream contract, the
orchestrator's opt-in streaming mode emitting `token` events, and the OpenAI
adapter's reassembly of streamed tool-call fragments. No key, no network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from engine.orchestrator import Orchestrator
from engine.registry import registry
from providers.base import Provider, Response, ToolCall, Usage

import engine.builtin.filesystem  # noqa: F401


def test_base_stream_default_wraps_complete():
    """A provider that doesn't override stream() still works through the
    streaming path: one terminal response, no deltas."""
    class Plain(Provider):
        def complete(self, messages, tools):
            return Response(text="hi", tool_calls=[], assistant_message={"role": "assistant", "content": "hi"})

    events = list(Plain().stream([], []))
    assert events == [("response", events[0][1])]
    assert events[0][1].text == "hi"
    print("  base Provider.stream default = one terminal response (no deltas) OK")


class StreamingFake(Provider):
    """Streams a few text deltas, then a final answer (no tool calls)."""
    def __init__(self, chunks):
        self._chunks = chunks

    def complete(self, messages, tools):
        text = "".join(self._chunks)
        return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})

    def stream(self, messages, tools):
        for c in self._chunks:
            yield ("delta", c)
        yield ("response", self.complete(messages, tools))


def test_orchestrator_emits_token_events_when_streaming():
    events = []
    agent = Orchestrator(
        StreamingFake(["Hel", "lo, ", "world"]), registry, Config(permission_mode="auto"),
        on_event=lambda kind, *d: events.append((kind, d)), stream=True,
    )
    answer = agent.run("hi")
    tokens = [d[0] for k, d in events if k == "token"]
    assert tokens == ["Hel", "lo, ", "world"], tokens
    assert answer == "Hello, world"
    print("  orchestrator streams token events + returns the full answer OK")


def test_orchestrator_no_tokens_when_not_streaming():
    events = []
    agent = Orchestrator(
        StreamingFake(["a", "b"]), registry, Config(permission_mode="auto"),
        on_event=lambda kind, *d: events.append((kind, d)),  # stream defaults False
    )
    answer = agent.run("hi")
    assert not any(k == "token" for k, _ in events), "no token events without stream=True"
    assert answer == "ab"
    print("  default (non-streaming) mode emits no token events OK")


def test_openai_stream_reassembles_text_and_tool_calls():
    """The OpenAI adapter must reassemble content deltas AND fragmented
    tool-call arguments from a streamed response."""
    from providers.openai_provider import OpenAIProvider

    # Minimal duck-typed chunk objects mimicking the OpenAI streaming shape.
    class D:  # generic attr bag
        def __init__(self, **kw): self.__dict__.update(kw)

    def content_chunk(text):
        return D(choices=[D(delta=D(content=text, tool_calls=None))], usage=None)

    def tool_chunk(index, tid=None, name=None, args=None):
        fn = D(name=name, arguments=args)
        tc = D(index=index, id=tid, function=fn)
        return D(choices=[D(delta=D(content=None, tool_calls=[tc]))], usage=None)

    usage_chunk = D(choices=[], usage=D(prompt_tokens=5, completion_tokens=7))

    chunks = [
        content_chunk("Let me "),
        content_chunk("check."),
        tool_chunk(0, tid="call_1", name="list_dir", args='{"pa'),
        tool_chunk(0, args='th": "."}'),  # arguments arrive in fragments
        usage_chunk,
    ]

    provider = OpenAIProvider(model="gpt-4o", api_key="x")
    # Patch the SDK call to return our fake chunk iterator.
    provider.client.chat.completions.create = lambda **kw: iter(chunks)

    deltas, final = [], None
    for kind, payload in provider.stream([], []):
        if kind == "delta":
            deltas.append(payload)
        else:
            final = payload

    assert deltas == ["Let me ", "check."], deltas
    assert final.text == "Let me check."
    assert len(final.tool_calls) == 1
    tc = final.tool_calls[0]
    assert tc.id == "call_1" and tc.name == "list_dir"
    assert tc.arguments == {"path": "."}, tc.arguments  # reassembled + parsed
    assert final.usage.prompt_tokens == 5 and final.usage.completion_tokens == 7
    print("  OpenAI adapter reassembles streamed text + fragmented tool-call args OK")


def test_openai_stream_falls_back_when_stream_options_rejected():
    """An OpenAI-compatible endpoint that rejects stream_options must still
    stream: drop the field and retry, rather than failing the turn."""
    import httpx
    import openai
    from providers.openai_provider import OpenAIProvider

    class D:
        def __init__(self, **kw): self.__dict__.update(kw)

    chunk = D(choices=[D(delta=D(content="hi", tool_calls=None))], usage=None)
    calls = {"n": 0, "had_stream_options": []}
    _resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))

    def fake_create(**kw):
        calls["n"] += 1
        calls["had_stream_options"].append("stream_options" in kw)
        if calls["n"] == 1:  # first attempt (with stream_options) is rejected
            raise openai.BadRequestError("stream_options unsupported", response=_resp, body=None)
        return iter([chunk])  # retry (without stream_options) succeeds

    provider = OpenAIProvider(model="compat/model", api_key="x")
    provider.client.chat.completions.create = fake_create
    deltas = [p for k, p in provider.stream([], []) if k == "delta"]
    assert deltas == ["hi"], deltas
    assert calls["n"] == 2, "must retry once"
    assert calls["had_stream_options"] == [True, False], calls["had_stream_options"]
    print("  OpenAI stream falls back gracefully when stream_options is rejected OK")


def main():
    test_base_stream_default_wraps_complete()
    test_orchestrator_emits_token_events_when_streaming()
    test_orchestrator_no_tokens_when_not_streaming()
    test_openai_stream_reassembles_text_and_tool_calls()
    test_openai_stream_falls_back_when_stream_options_rejected()
    print("STREAMING TESTS PASSED")


if __name__ == "__main__":
    main()
