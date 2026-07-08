"""The tool registry and dispatcher.

A tool is a plain Python function plus a schema describing it to the model.
The registry holds them, hands their schemas to the provider, and runs the one
the model asks for. Adding a new company capability = registering another Tool.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments the model must supply
    handler: Callable[..., str]  # the actual function; must return a string
    risk: str = "safe"  # safe | write | dangerous  (drives the permission layer)


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[dict]:
        """The tool definitions in the format the model expects."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def run(self, name: str, arguments: dict) -> str:
        """Execute a tool by name. Always returns a string for the model to read."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return tool.handler(**arguments)
        except TypeError as exc:
            return f"Error: bad arguments for '{name}': {exc}"
        except Exception as exc:  # tools must never crash the loop
            return f"Error running '{name}': {exc}"


# A single shared registry. Tool modules import this and register onto it.
registry = Registry()
