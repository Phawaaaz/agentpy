"""Multi-agent tests: FilteredRegistry, role loading, and a full
coordinator -> delegate -> sub-agent run against a scripted FakeProvider
(same pattern as tests/smoke_test.py). No key, no network.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from engine.orchestrator import Orchestrator
from engine.registry import Registry, Tool
from multiagent.coordinator import FilteredRegistry, build_delegate_tool
from multiagent.roles import AgentRole, load_roles
from providers.base import Provider, Response, ToolCall


class FakeProvider(Provider):
    """Shared between the coordinator and every sub-agent it spawns --
    calls are consumed strictly in the order they happen across both."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def complete(self, messages, tools):
        if self._i >= len(self._script):
            raise AssertionError(f"FakeProvider script exhausted after {self._i} calls")
        turn = self._script[self._i]
        self._i += 1
        return turn


def _tool_turn(call_id, name, arguments):
    return Response(
        text=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
            ],
        },
    )


def _final_turn(text):
    return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})


def _make_registry():
    registry = Registry()
    registry.register(
        Tool(
            name="noop",
            description="does nothing",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "noop ran",
            risk="safe",
        )
    )
    return registry


def test_filtered_registry_hides_only_named_tools():
    source = _make_registry()
    source.register(Tool(name="delegate", description="x", parameters={"type": "object", "properties": {}}, handler=lambda: "x", risk="dangerous"))
    view = FilteredRegistry(source, hidden={"delegate"})

    assert view.get("noop") is not None
    assert view.get("delegate") is None
    assert {t.name for t in view.all()} == {"noop"}
    assert {s["function"]["name"] for s in view.specs()} == {"noop"}
    assert view.run("noop", {}) == "noop ran"
    assert "unknown tool" in view.run("delegate", {})

    # Live view: a tool added to `source` after construction is visible too.
    source.register(Tool(name="later", description="x", parameters={"type": "object", "properties": {}}, handler=lambda: "later ran", risk="safe"))
    assert view.get("later") is not None
    print("  FilteredRegistry hides/exposes correctly and stays live OK")


def test_load_roles():
    with tempfile.TemporaryDirectory() as d:
        assert load_roles(os.path.join(d, "missing.json")) == {}

        path = os.path.join(d, "roles.json")
        with open(path, "w") as f:
            json.dump(
                {
                    "roles": {
                        "researcher": {"description": "reads code", "system_prompt": "You research."},
                        "reviewer": {"description": "reviews code", "system_prompt": "You review."},
                    }
                },
                f,
            )
        roles = load_roles(path)
        assert set(roles) == {"researcher", "reviewer"}
        assert roles["researcher"].system_prompt == "You research."
        print("  load_roles OK")


def test_delegate_unknown_role_returns_error_string():
    registry = _make_registry()
    provider = FakeProvider([])  # never called -- unknown role short-circuits
    tool = build_delegate_tool(
        provider, registry, Config(permission_mode="auto"),
        roles={"researcher": AgentRole("researcher", "reads code", "You research.")},
        approver=lambda call, t: True,
    )
    result = tool.handler(role="nonexistent", task="do something")
    assert "unknown role" in result and "researcher" in result
    print("  unknown role handled without raising OK")


def test_coordinator_delegates_to_subagent_which_uses_its_own_tool():
    registry = _make_registry()
    script = [
        _tool_turn("c1", "delegate", {"role": "reviewer", "task": "review the change"}),
        _tool_turn("s1", "noop", {}),
        _final_turn("sub-agent finished\n<promise>COMPLETE</promise>"),
        _final_turn("coordinator done: reviewer says it's fine"),
    ]
    provider = FakeProvider(script)

    delegate_tool = build_delegate_tool(
        provider, registry, Config(permission_mode="auto"),
        roles={"reviewer": AgentRole("reviewer", "reviews code", "You are a meticulous reviewer.")},
        approver=lambda call, t: True,
    )
    registry.register(delegate_tool)

    coordinator = Orchestrator(provider, registry, Config(permission_mode="auto"))
    answer = coordinator.run("get this change reviewed")

    assert answer == "coordinator done: reviewer says it's fine"
    print("  coordinator -> delegate -> sub-agent (using its own tool) OK")


def test_subagent_cannot_recursively_delegate():
    """Even if a (misbehaving) script has the sub-agent try to call
    'delegate', its registry doesn't have it -- it gets an error string
    back, never a real recursive sub-sub-agent."""
    registry = _make_registry()
    script = [
        _tool_turn("c1", "delegate", {"role": "reviewer", "task": "review, then try to delegate further"}),
        _tool_turn("s1", "delegate", {"role": "reviewer", "task": "recurse"}),  # sub-agent attempts recursion
        _final_turn("sub-agent gave up on recursing\n<promise>COMPLETE</promise>"),
        _final_turn("coordinator done"),
    ]
    provider = FakeProvider(script)
    delegate_tool = build_delegate_tool(
        provider, registry, Config(permission_mode="auto"),
        roles={"reviewer": AgentRole("reviewer", "reviews code", "You review.")},
        approver=lambda call, t: True,
    )
    registry.register(delegate_tool)

    coordinator = Orchestrator(provider, registry, Config(permission_mode="auto"))
    answer = coordinator.run("get this reviewed")
    assert answer == "coordinator done"
    print("  sub-agent cannot recursively delegate OK")


def main():
    test_filtered_registry_hides_only_named_tools()
    test_load_roles()
    test_delegate_unknown_role_returns_error_string()
    test_coordinator_delegates_to_subagent_which_uses_its_own_tool()
    test_subagent_cannot_recursively_delegate()
    print("MULTIAGENT TESTS PASSED")


if __name__ == "__main__":
    main()
