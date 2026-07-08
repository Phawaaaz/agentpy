"""Smoke test: exercise the full agent loop with a fake model (no API key needed).

The fake provider returns a scripted sequence of turns:
  1. call list_dir
  2. call write_file  (a 'write' risk action)
  3. finish with a text answer

This proves the loop, the registry/dispatcher, the permission layer, and the
message plumbing all work together — independent of any real model.
"""

import os
import sys

# Make the project root importable when run as `python tests/smoke_test.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from core.orchestrator import Orchestrator
from providers.base import Provider, Response, ToolCall
from tools.registry import registry

import tools.filesystem  # noqa: F401  (registers tools)
import tools.shell  # noqa: F401


class FakeProvider(Provider):
    """Returns a fixed script of turns instead of calling a real model."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def complete(self, messages, tools):
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
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        },
    )


def _final_turn(text):
    return Response(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text})


def main():
    out_path = os.path.join(os.path.dirname(__file__), "_smoke_out.txt")
    script = [
        _tool_turn("c1", "list_dir", {"path": "."}),
        _tool_turn("c2", "write_file", {"path": out_path, "content": "hello from the agent"}),
        _final_turn("Done: listed the directory and wrote a file."),
    ]

    config = Config(permission_mode="auto")  # auto so the write runs unattended
    events = []
    agent = Orchestrator(
        FakeProvider(script),
        registry,
        config,
        on_event=lambda kind, *d: events.append((kind, d)),
    )

    answer = agent.run("do the scripted task")

    # Assertions: the loop finished, the file got written, events fired.
    assert answer.startswith("Done:"), f"unexpected answer: {answer!r}"
    assert os.path.exists(out_path), "write_file tool did not create the file"
    with open(out_path) as f:
        assert f.read() == "hello from the agent"
    kinds = [k for k, _ in events]
    assert "tool_call" in kinds and "tool_result" in kinds, kinds
    os.remove(out_path)

    print("SMOKE TEST PASSED")
    print(f"  final answer : {answer}")
    print(f"  events fired : {kinds}")
    print(f"  tools loaded : {[t.name for t in registry.all()]}")


if __name__ == "__main__":
    main()
