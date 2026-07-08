"""The Provider interface.

The orchestrator only ever talks to this abstraction, never to a specific model
SDK. That is what makes the harness model-independent: swap the implementation
below and the rest of the system doesn't change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolCall:
    """A request from the model to run one tool."""

    id: str  # unique id the model gave this call; results are matched back by it
    name: str  # which tool to run
    arguments: dict  # parsed keyword arguments for the tool


@dataclass
class Usage:
    """Token counts for one model call (used for cost/observability)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Response:
    """One normalized turn back from the model."""

    text: str | None  # any natural-language text the model produced
    tool_calls: list[ToolCall]  # tools it wants to run (empty => it's done)
    assistant_message: dict  # the message to append to history (chat format)
    usage: Usage | None = None  # token usage for this call, if the API reported it


class Provider(ABC):
    """Anything that can turn a conversation into the model's next turn."""

    @abstractmethod
    def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        """Send the conversation + available tools, get the model's next turn."""
        raise NotImplementedError
