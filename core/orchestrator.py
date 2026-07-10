"""The orchestrator — the agent loop.

This is the heart of the harness: observe -> think -> act -> repeat. It is the
only component that talks to the model, and every tool run passes through its
permission check before executing.
"""

from typing import Callable

from config import Config
from core import permissions
from core.context import Conversation
from observability.usage import UsageTracker
from providers.base import Provider, ToolCall
from tools.registry import Registry, Tool

# Called when a tool needs human approval. Returns True to allow, False to deny.
Approver = Callable[[ToolCall, Tool], bool]
# Called to report progress to the interface (kind, *details).
EventHook = Callable[..., None]


class Orchestrator:
    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        config: Config,
        approver: Approver | None = None,
        on_event: EventHook | None = None,
        conversation: Conversation | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.config = config
        self.approver = approver or (lambda tc, tool: True)
        self.on_event = on_event or (lambda *args, **kwargs: None)
        # Context is a separate concern; default to a plain (non-compacting) one.
        self.conversation = conversation or Conversation(config.system_prompt)
        self.usage = usage_tracker

    def run(self, user_input: str) -> str:
        """Run one user request to completion and return the final answer."""
        self.conversation.add({"role": "user", "content": user_input})

        for _ in range(self.config.max_steps):
            # Keep history within the model's context window before each call.
            if self.conversation.maybe_compact():
                self.on_event("compacted", self.conversation.estimated_tokens())

            response = self.provider.complete(
                self.conversation.to_list(), self.registry.specs()
            )
            self.conversation.add(response.assistant_message)

            if self.usage is not None and response.usage is not None:
                self.usage.record(self.config.model, response.usage)
                self.on_event(
                    "usage",
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )

            # No tool calls => the model is done; the interface prints the
            # answer itself, so don't also emit it as "thinking".
            if not response.tool_calls:
                return response.text or ""

            # The model may narrate its reasoning alongside its tool calls.
            if response.text and response.text.strip():
                self.on_event("thinking", response.text.strip())

            # Otherwise run each requested tool and feed results back in.
            for tool_call in response.tool_calls:
                result = self._handle_tool_call(tool_call)
                self.conversation.add(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        return "[stopped: reached the maximum number of steps]"

    def _handle_tool_call(self, tool_call: ToolCall) -> str:
        tool = self.registry.get(tool_call.name)
        self.on_event("tool_call", tool_call.name, tool_call.arguments)

        if tool is None:
            return f"Error: unknown tool '{tool_call.name}'"

        decision = permissions.check(
            tool, tool_call.arguments, self.config.permission_mode
        )

        if decision == permissions.ASK:
            if not self.approver(tool_call, tool):
                self.on_event("denied", tool_call.name)
                return "Action denied by the user."
        elif decision == permissions.DENY:
            self.on_event("denied", tool_call.name)
            return (
                f"Action blocked by permission policy "
                f"(mode={self.config.permission_mode}, risk={tool.risk})."
            )

        result = self.registry.run(tool_call.name, tool_call.arguments)
        self.on_event("tool_result", tool_call.name, result)
        return result
