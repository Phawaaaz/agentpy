"""Hooks: pluggable interception points around the agent loop (D32).

The loop's rule is "capability grows at the edges" -- but until now,
*cross-cutting* behavior (a guardrail that vetoes a tool call, a redaction
pass over model output, a context injector) had nowhere to go except
editing the orchestrator, exactly what AGENTS.md forbids. `Hooks` is the
extension point that fixes that: four ordered lists of plain callables the
orchestrator runs at fixed places. An empty `Hooks()` (the default) leaves
the loop byte-for-byte identical to before this existed.

The four points, in loop order:

- pre_model_call(messages) -> messages      shape/augment what the model sees
- post_model_call(response) -> response     rewrite/inspect what it said
- pre_tool_call(tool_call, tool) -> ToolCall | str
      return the (possibly modified) call to proceed, or a string to veto:
      the string becomes the tool result the model sees, and the tool never
      runs -- the same "denial is an observation, not a crash" contract as
      the permission layer (D5/D6).
- post_tool_call(tool_call, result) -> result   transform tool output

Hooks run in list order, each feeding the next. Like approver/on_event
(D7), these are small callables rather than a middleware class -- an
interface asks for exactly what it needs and nothing more.
"""

from dataclasses import dataclass, field
from typing import Callable, Union

from engine.registry import Tool
from providers.base import Response, ToolCall

PreModelHook = Callable[[list[dict]], list[dict]]
PostModelHook = Callable[[Response], Response]
PreToolHook = Callable[[ToolCall, Tool], Union[ToolCall, str]]
PostToolHook = Callable[[ToolCall, str], str]


@dataclass
class Hooks:
    """Ordered interception points. All default empty: no hooks, no change."""

    pre_model_call: list[PreModelHook] = field(default_factory=list)
    post_model_call: list[PostModelHook] = field(default_factory=list)
    pre_tool_call: list[PreToolHook] = field(default_factory=list)
    post_tool_call: list[PostToolHook] = field(default_factory=list)
