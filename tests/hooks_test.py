"""Tests for the hooks/middleware layer (D32): each of the four
interception points actually intercepts, a pre-tool veto stops the tool
from running, and an empty Hooks() leaves the loop's behavior unchanged.
Runs the REAL Orchestrator against a scripted fake provider -- no key, no
network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from engine.hooks import Hooks
from engine.orchestrator import Orchestrator
from engine.registry import registry
from providers.base import Provider, Response, ToolCall

import engine.builtin.filesystem  # noqa: F401  (registers write_file)


class FakeProvider(Provider):
    def __init__(self, script):
        self._script = list(script)
        self.seen_messages: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen_messages.append(messages)
        return self._script.pop(0)


def _tool_turn(call_id, name, arguments):
    return Response(
        text=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        assistant_message={"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}]},
    )


def _final(text):
    return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})


def test_pre_tool_hook_vetoes_execution():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "blocked.txt")

        def deny_writes(tool_call, tool):
            if tool_call.name == "write_file":
                return "Vetoed by policy hook: writes are disabled here."
            return tool_call

        provider = FakeProvider([
            _tool_turn("c1", "write_file", {"path": target, "content": "nope"}),
            _final("done"),
        ])
        events = []
        agent = Orchestrator(
            provider, registry, Config(permission_mode="auto"),
            on_event=lambda kind, *d: events.append(kind),
            hooks=Hooks(pre_tool_call=[deny_writes]),
        )
        agent.run("try to write")
        assert not os.path.exists(target), "the vetoed tool must never run"
        # The veto string became the tool result the model saw next turn.
        tool_msg = [m for m in provider.seen_messages[1] if m.get("role") == "tool"][0]
        assert "Vetoed by policy hook" in tool_msg["content"]
        assert "denied" in events
    print("  pre_tool_call veto blocks execution and feeds the reason back OK")


def test_pre_tool_hook_can_rewrite_arguments():
    with tempfile.TemporaryDirectory() as tmp:
        intended = os.path.join(tmp, "a.txt")
        redirected = os.path.join(tmp, "b.txt")

        def redirect(tool_call, tool):
            if tool_call.name == "write_file":
                tool_call.arguments = dict(tool_call.arguments, path=redirected)
            return tool_call

        provider = FakeProvider([
            _tool_turn("c1", "write_file", {"path": intended, "content": "hi"}),
            _final("done"),
        ])
        agent = Orchestrator(
            provider, registry, Config(permission_mode="auto"),
            hooks=Hooks(pre_tool_call=[redirect]),
        )
        agent.run("write")
        assert os.path.exists(redirected) and not os.path.exists(intended)
    print("  pre_tool_call can rewrite a call before it runs OK")


def test_pre_and_post_model_hooks():
    def inject_context(messages):
        return messages + [{"role": "user", "content": "INJECTED-CONTEXT"}]

    def redact(response):
        if response.text and "SECRET" in response.text:
            response.text = response.text.replace("SECRET", "[redacted]")
        return response

    provider = FakeProvider([_final("the SECRET is out")])
    agent = Orchestrator(
        provider, registry, Config(permission_mode="auto"),
        hooks=Hooks(pre_model_call=[inject_context], post_model_call=[redact]),
    )
    answer = agent.run("hello")
    assert provider.seen_messages[0][-1]["content"] == "INJECTED-CONTEXT"
    assert answer == "the [redacted] is out"
    print("  pre_model_call injects context; post_model_call rewrites output OK")


def test_post_tool_hook_transforms_results():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "x.txt")

        def stamp(tool_call, result):
            return result + " [audited]"

        provider = FakeProvider([
            _tool_turn("c1", "write_file", {"path": target, "content": "hi"}),
            _final("done"),
        ])
        agent = Orchestrator(
            provider, registry, Config(permission_mode="auto"),
            hooks=Hooks(post_tool_call=[stamp]),
        )
        agent.run("write")
        tool_msg = [m for m in provider.seen_messages[1] if m.get("role") == "tool"][0]
        assert tool_msg["content"].endswith("[audited]")
    print("  post_tool_call transforms the result the model sees OK")


def test_empty_hooks_change_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "plain.txt")
        provider = FakeProvider([
            _tool_turn("c1", "write_file", {"path": target, "content": "plain"}),
            _final("done"),
        ])
        agent = Orchestrator(provider, registry, Config(permission_mode="auto"))
        answer = agent.run("write")
        assert answer == "done"
        assert open(target, encoding="utf-8").read() == "plain"
    print("  default (empty) hooks leave the loop's behavior unchanged OK")


def main():
    test_pre_tool_hook_vetoes_execution()
    test_pre_tool_hook_can_rewrite_arguments()
    test_pre_and_post_model_hooks()
    test_post_tool_hook_transforms_results()
    test_empty_hooks_change_nothing()
    print("HOOKS TESTS PASSED")


if __name__ == "__main__":
    main()
